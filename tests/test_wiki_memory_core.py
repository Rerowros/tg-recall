from __future__ import annotations

import os
from pathlib import Path

import pytest

from tg_recall.wiki_memory import (
    AssertionKind,
    AuthorizedWikiScope,
    Freshness,
    PageKind,
    RawSourceRecord,
    WikiAssertion,
    WikiMemoryError,
    WikiMemoryStore,
    WikiPageDraft,
    select_delta,
)


@pytest.fixture
def scope() -> AuthorizedWikiScope:
    return AuthorizedWikiScope("work", "team-2026", (100,))


def record(message_id: int, timestamp: str, text: str, source_version: str = "1") -> RawSourceRecord:
    return RawSourceRecord(
        citation=f"tg://chat/100/message/{message_id}",
        chat_id=100,
        timestamp=timestamp,
        text=text,
        source_id=f"message-{message_id}",
        source_version=source_version,
    )


def page(*assertions: WikiAssertion) -> WikiPageDraft:
    return WikiPageDraft(PageKind.PERSON, "alice", "Alice", tuple(assertions))


def observed(citation: str, text: str = "Alice prefers concise written updates.") -> WikiAssertion:
    return WikiAssertion(text, AssertionKind.OBSERVED, 0.95, (citation,))


def test_compile_writes_immutable_delta_snapshots_and_versioned_markdown_pages(tmp_path: Path, scope: AuthorizedWikiScope) -> None:
    store = WikiMemoryStore(tmp_path / "profile-wiki")
    first = record(1, "2026-08-01T10:00:00Z", "Please send a concise update.")
    first_result = store.compile(scope, [first], [page(observed(first.citation))], created_at="2026-08-01T11:00:00Z")

    assert first_result.delta_count == 1
    assert first_result.snapshot is not None
    assert len(first_result.page_revisions) == 1
    first_markdown = next((store.root / "pages").rglob("*.md"))
    content = first_markdown.read_text(encoding="utf-8")
    assert "## Observed" in content
    assert "## Hypotheses" in content
    assert first.citation in content
    assert str(store.root) not in content

    second = record(2, "2026-08-02T10:00:00Z", "A short summary is enough.")
    second_assertion = WikiAssertion(
        "Alice asks for concise written updates.",
        AssertionKind.OBSERVED,
        0.98,
        tuple(sorted((first.citation, second.citation))),
    )
    second_result = store.compile(scope, [first, second], [page(second_assertion)], created_at="2026-08-02T11:00:00Z")

    assert second_result.delta_count == 1
    assert second_result.snapshot is not None
    assert second_result.snapshot.snapshot_id != first_result.snapshot.snapshot_id
    assert len(list((store.root / "raw" / scope.profile_id / scope.scope_id).glob("*.jsonl"))) == 2
    assert len(list((store.root / "pages" / scope.profile_id / scope.scope_id / "people" / "alice").glob("*.md"))) == 2
    assert first_markdown.exists(), "earlier revision must not be overwritten"


def test_delta_selection_uses_scope_local_success_cursor_and_rejects_other_chat(tmp_path: Path, scope: AuthorizedWikiScope) -> None:
    first = record(1, "2026-08-01T10:00:00Z", "first")
    store = WikiMemoryStore(tmp_path / "wiki")
    # This store is never compiled; the explicit data-only selector has no I/O.
    manifest = store._make_snapshot(scope, (first,), "2026-08-01T11:00:00Z")
    second = record(2, "2026-08-01T10:00:00Z", "same timestamp but new source")

    assert select_delta([first, second], scope, manifest) == (second,)
    outside = RawSourceRecord("tg://chat/999/message/1", 999, "2026-08-02T00:00:00Z", "outside", "message-999")
    with pytest.raises(WikiMemoryError, match="outside the authorized"):
        select_delta([outside], scope, manifest)


def test_same_cursor_source_version_change_is_a_delta_and_stales_prior_pages(tmp_path: Path, scope: AuthorizedWikiScope) -> None:
    store = WikiMemoryStore(tmp_path / "wiki")
    original = record(1, "2026-08-01T10:00:00Z", "Please use concise updates.", "v1")
    store.compile(scope, [original], [page(observed(original.citation))], created_at="2026-08-01T11:00:00Z")
    edited = record(1, "2026-08-01T10:00:00Z", "Please use structured updates.", "v2")

    result = store.compile(scope, [edited], [], created_at="2026-08-02T11:00:00Z")

    assert result.delta_count == 1
    assert store.lookup(scope, "concise")[0].freshness == Freshness.STALE


def test_missing_committed_source_requires_explicit_invalidation(tmp_path: Path, scope: AuthorizedWikiScope) -> None:
    store = WikiMemoryStore(tmp_path / "wiki")
    source = record(1, "2026-08-01T10:00:00Z", "Please use concise updates.")
    store.compile(scope, [source], [page(observed(source.citation))], created_at="2026-08-01T11:00:00Z")

    with pytest.raises(WikiMemoryError, match="explicit deletion invalidation"):
        store.compile(scope, [], [], created_at="2026-08-02T11:00:00Z")


def test_hypotheses_and_observations_are_structured_with_exact_citations(tmp_path: Path, scope: AuthorizedWikiScope) -> None:
    store = WikiMemoryStore(tmp_path / "wiki")
    source = record(1, "2026-08-01T10:00:00Z", "Maybe calls are preferred.")
    draft = page(
        observed(source.citation),
        WikiAssertion("Alice may prefer calls for urgent topics.", AssertionKind.HYPOTHESIS, 0.4, (source.citation,)),
    )
    result = store.compile(scope, [source], [draft], created_at="2026-08-01T11:00:00Z")
    revision = result.page_revisions[0]

    assert [assertion.kind for assertion in revision.draft.assertions] == [AssertionKind.OBSERVED, AssertionKind.HYPOTHESIS]
    assert all(assertion.citations == (source.citation,) for assertion in revision.draft.assertions)
    with pytest.raises(WikiMemoryError, match="exact tg"):
        WikiAssertion("not enough", AssertionKind.HYPOTHESIS, 0.2, ("https://example.test",))


def test_lookup_is_compact_and_marks_page_stale_after_newer_raw_snapshot(tmp_path: Path, scope: AuthorizedWikiScope) -> None:
    store = WikiMemoryStore(tmp_path / "wiki")
    first = record(1, "2026-08-01T10:00:00Z", "Please use concise updates.")
    store.compile(scope, [first], [page(observed(first.citation))], created_at="2026-08-01T11:00:00Z")
    current = store.lookup(scope, "concise")

    assert len(current) == 1
    assert current[0].freshness == Freshness.CURRENT
    assert current[0].citations == (first.citation,)
    assert len(current[0].excerpt) <= 600

    newer = record(2, "2026-08-02T10:00:00Z", "New raw evidence exists.")
    store.compile(scope, [first, newer], [], created_at="2026-08-02T11:00:00Z")
    stale = store.lookup(scope, "concise")

    assert stale[0].freshness == Freshness.STALE


def test_lookup_then_expansion_uses_only_injected_read_only_authorized_resolver(tmp_path: Path, scope: AuthorizedWikiScope) -> None:
    store = WikiMemoryStore(tmp_path / "wiki")
    source = record(1, "2026-08-01T10:00:00Z", "Please send concise updates.")
    assertion = observed(source.citation)
    store.compile(scope, [source], [page(assertion)], created_at="2026-08-01T11:00:00Z")
    hit = store.lookup(scope, "concise")[0]
    calls: list[tuple[AuthorizedWikiScope, tuple[str, ...], int]] = []

    def resolver(received_scope: AuthorizedWikiScope, citations: tuple[str, ...], limit: int) -> tuple[RawSourceRecord, ...]:
        calls.append((received_scope, citations, limit))
        return (source,)

    expanded = store.expand_assertion(scope, hit.assertions[0], resolver, limit=1)

    assert expanded == (source,)
    assert calls == [(scope, (source.citation,), 1)]


def test_profile_and_scope_directories_isolate_pages_even_under_one_caller_owned_root(tmp_path: Path, scope: AuthorizedWikiScope) -> None:
    store = WikiMemoryStore(tmp_path / "wiki")
    work = record(1, "2026-08-01T10:00:00Z", "work concise updates")
    personal_scope = AuthorizedWikiScope("personal", "team-2026", (100,))
    personal = record(2, "2026-08-01T10:00:00Z", "personal travel plans")

    store.compile(scope, [work], [page(observed(work.citation))], created_at="2026-08-01T11:00:00Z")
    store.compile(personal_scope, [personal], [WikiPageDraft(PageKind.PROJECT, "trip", "Trip", (observed(personal.citation, "Travel plans are discussed."),))], created_at="2026-08-01T11:00:00Z")

    assert store.lookup(scope, "concise")
    assert store.lookup(scope, "travel") == ()
    assert store.lookup(personal_scope, "travel")


def test_load_revisions_accepts_only_exact_committed_hash_verified_ids(tmp_path: Path, scope: AuthorizedWikiScope) -> None:
    store = WikiMemoryStore(tmp_path / "wiki")
    source = record(1, "2026-08-01T10:00:00Z", "source")
    compiled = store.compile(scope, [source], [page(observed(source.citation))], created_at="2026-08-01T11:00:00Z")
    revision_id = compiled.page_revisions[0].revision_id

    loaded = store.load_revisions(scope, [revision_id])
    assert loaded[0].revision_id == revision_id
    assert "Alice" in store.render_revision(loaded[0])

    path = next((store.root / "pages").rglob(f"{revision_id}.md"))
    path.write_text(path.read_text(encoding="utf-8").replace("# Alice", "# Tampered"), encoding="utf-8")
    with pytest.raises(WikiMemoryError, match="immutable revision ID"):
        store.load_revisions(scope, [revision_id])


def test_store_refuses_symlink_escape_and_resolver_scope_escape(tmp_path: Path, scope: AuthorizedWikiScope) -> None:
    root = tmp_path / "wiki"
    store = WikiMemoryStore(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    raw = root / "raw"
    try:
        raw.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows runner")
    source = record(1, "2026-08-01T10:00:00Z", "safe")
    with pytest.raises(WikiMemoryError, match="symbolic link|escapes"):
        store.compile(scope, [source], [page(observed(source.citation))], created_at="2026-08-01T11:00:00Z")

    # A resolver cannot inject a citation or chat outside the assertion/scope.
    clean_store = WikiMemoryStore(tmp_path / "clean")
    clean_store.compile(scope, [source], [page(observed(source.citation))], created_at="2026-08-01T11:00:00Z")
    assertion = clean_store.lookup(scope, "concise")[0].assertions[0]
    wrong = RawSourceRecord("tg://chat/100/message/2", 100, "2026-08-01T10:01:00Z", "wrong", "message-2")
    with pytest.raises(WikiMemoryError, match="outside the authorized assertion"):
        clean_store.expand_assertion(scope, assertion, lambda *_args: (wrong,))


def test_raw_record_rejects_citation_whose_chat_identity_does_not_match_chat_id() -> None:
    with pytest.raises(WikiMemoryError, match="must match record chat_id"):
        RawSourceRecord("tg://chat/101/message/1", 100, "2026-08-01T10:00:00Z", "mismatch", "message-1")


def test_interrupted_commit_keeps_orphan_revision_invisible_and_a_deterministic_retry_commits_it(tmp_path: Path, scope: AuthorizedWikiScope) -> None:
    root = tmp_path / "wiki"
    first = record(1, "2026-08-01T10:00:00Z", "first")
    initial = WikiMemoryStore(root)
    initial.compile(scope, [first], [page(observed(first.citation, "Alice prefers updates."))], created_at="2026-08-01T11:00:00Z")
    prior_revision = next((root / "pages").rglob("*.md"))
    second = record(2, "2026-08-02T10:00:00Z", "second")
    second_draft = page(WikiAssertion("Alice now prefers structured updates.", AssertionKind.OBSERVED, 0.9, tuple(sorted((first.citation, second.citation)))))

    def fail_manifest(source: Path, destination: Path) -> None:
        if destination.name.endswith(".manifest.json"):
            raise OSError("injected manifest move failure")
        os.replace(source, destination)

    interrupted = WikiMemoryStore(root, replace_file=fail_manifest)
    with pytest.raises(OSError, match="injected"):
        interrupted.compile(scope, [first, second], [second_draft], created_at="2026-08-02T11:00:00Z")

    assert interrupted.lookup(scope, "structured") == (), "uncommitted page revision must remain invisible"
    assert interrupted.lookup(scope, "prefers updates")[0].revision_id in prior_revision.name

    raw_dir = root / "raw" / scope.profile_id / scope.scope_id
    orphan_raw = next(path for path in raw_dir.glob("*.jsonl") if not (raw_dir / f"{path.stem}.manifest.json").exists())
    expected_orphan = orphan_raw.read_bytes()
    orphan_raw.write_bytes(b"conflicting orphan payload\n")
    with pytest.raises(WikiMemoryError, match="conflicts"):
        WikiMemoryStore(root).compile(scope, [first, second], [second_draft], created_at="2026-08-02T11:00:00Z")
    assert orphan_raw.read_bytes() == b"conflicting orphan payload\n", "retry must not overwrite a conflicting orphan"
    orphan_raw.write_bytes(expected_orphan)

    retried = WikiMemoryStore(root)
    committed = retried.compile(scope, [first, second], [second_draft], created_at="2026-08-02T11:00:00Z")

    assert committed.snapshot is not None
    assert retried.lookup(scope, "structured")[0].freshness == Freshness.CURRENT
    assert prior_revision.exists(), "a committed revision must never be overwritten by retry"


def test_scope_identity_includes_the_normalized_chat_set_and_prevents_page_mixing(tmp_path: Path, scope: AuthorizedWikiScope) -> None:
    store = WikiMemoryStore(tmp_path / "wiki")
    source = record(1, "2026-08-01T10:00:00Z", "scope evidence")
    store.compile(scope, [source], [page(observed(source.citation, "Original scope assertion."))], created_at="2026-08-01T11:00:00Z")
    widened = AuthorizedWikiScope("work", "team-2026", (101, 100))
    assert widened.chat_ids == (100, 101)
    store.compile(
        widened,
        [source],
        [page(observed(source.citation, "Widened scope assertion."))],
        created_at="2026-08-01T11:00:00Z",
    )

    assert store.lookup(scope, "widened") == ()
    assert store.lookup(widened, "widened")


def test_compilation_rejects_duplicate_page_drafts_and_non_monotonic_success_timestamp(tmp_path: Path, scope: AuthorizedWikiScope) -> None:
    store = WikiMemoryStore(tmp_path / "wiki")
    first = record(1, "2026-08-01T10:00:00Z", "first")
    duplicate = page(observed(first.citation))
    with pytest.raises(WikiMemoryError, match="duplicate page drafts"):
        store.compile(scope, [first], [duplicate, duplicate], created_at="2026-08-01T11:00:00Z")

    store.compile(scope, [first], [duplicate], created_at="2026-08-01T11:00:00Z")
    second = record(2, "2026-08-02T10:00:00Z", "second")
    with pytest.raises(WikiMemoryError, match="must be newer"):
        store.compile(scope, [first, second], [page(observed(second.citation, "A newer assertion."))], created_at="2026-08-01T10:59:59Z")
