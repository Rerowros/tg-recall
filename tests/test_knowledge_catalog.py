from __future__ import annotations

import pytest

from tg_recall.knowledge_catalog import (
    Authority,
    CatalogHit,
    EvidenceMemberKind,
    EvidenceSetMember,
    EvidenceSetReference,
    ExpandedSource,
    Freshness,
    KnowledgeCatalog,
    KnowledgeCatalogError,
    KnowledgeLayer,
    KnowledgeScope,
    RawCatalogReference,
    ResearchCheckpoint,
    ResearchSession,
    SourceReference,
    VersionMap,
    WikiAssertionReference,
    expand_sources,
    plan_evidence_reuse,
    validate_session_metadata,
)


def source(message_id: int, version: str = "v1") -> SourceReference:
    return SourceReference(f"tg://chat/100/message/{message_id}", 100, "message", version)


@pytest.fixture
def scope() -> KnowledgeScope:
    return KnowledgeScope("work", (100,))


def wiki(scope: KnowledgeScope, raw: SourceReference, *, source_version: str = "v1", contradicted: bool = False) -> WikiAssertionReference:
    return WikiAssertionReference(
        assertion_id="decision-1",
        page_id="project-a",
        scope=scope,
        text="The payment deadline is Friday.",
        confidence=0.8,
        citations=(raw.citation,),
        source_versions=((raw.citation, source_version),),
        wiki_revision="wiki-v1",
        topics=("payment", "deadline"),
        contradicted_by=(raw.citation,) if contradicted else (),
    )


def evidence_set(scope: KnowledgeScope, raw: SourceReference) -> EvidenceSetReference:
    return EvidenceSetReference(
        evidence_set_id="payment-research",
        scope=scope,
        purpose="Verify payment deadline",
        query="payment deadline",
        members=(
            EvidenceSetMember("raw-member", EvidenceMemberKind.RAW, "message-1", (raw.citation,), "v1"),
            EvidenceSetMember("wiki-member", EvidenceMemberKind.WIKI, "project-a", (raw.citation,), "wiki-v1"),
        ),
        summary="A compact navigation note for the payment decision.",
        created_at="2026-08-03T10:00:00Z",
        revision="set-v1",
        topics=("payment", "deadline"),
    )


def test_catalog_is_profile_aware_returns_required_metadata_and_raw_wins_conflicts(scope: KnowledgeScope) -> None:
    raw = source(1, "v2")
    catalog = KnowledgeCatalog(
        scope,
        raw=(RawCatalogReference(raw, ("payment", "deadline")),),
        wiki=(wiki(scope, raw, source_version="v1", contradicted=True),),
        evidence_sets=(evidence_set(scope, raw),),
    )

    hits = catalog.lookup("payment deadline")

    assert [hit.layer for hit in hits] == [KnowledgeLayer.RAW, KnowledgeLayer.WIKI, KnowledgeLayer.EVIDENCE_SET]
    assert hits[0].authority == Authority.AUTHORITATIVE
    assert hits[0].summary is None, "raw content is referenced, not copied into the catalog"
    assert hits[1].authority == Authority.DERIVED
    assert hits[1].freshness == Freshness.CONTRADICTED
    assert hits[0].as_json() == {
        "authority": "authoritative",
        "citations": [raw.citation],
        "confidence": 1.0,
        "freshness": "current",
        "layer": "raw",
        "logical_id": "raw-100-1",
        "source_version": "v2",
        "summary": None,
    }


def test_wiki_staleness_and_selective_evidence_reuse_are_version_based(scope: KnowledgeScope) -> None:
    raw = source(1)
    current = VersionMap(raw=((raw.citation, "v1"),), wiki=(("project-a", "wiki-v1"),))
    changed = VersionMap(raw=((raw.citation, "v2"),), wiki=(("project-a", "wiki-v1"),))

    plan = plan_evidence_reuse(evidence_set(scope, raw), current)
    stale_plan = plan_evidence_reuse(evidence_set(scope, raw), changed)
    stale_catalog = KnowledgeCatalog(scope, raw=(RawCatalogReference(source(1, "v2"), ("payment",)),), wiki=(wiki(scope, raw),))

    assert plan.reusable is True
    assert stale_plan.reusable is False
    assert stale_plan.stale_member_ids == ("raw-member",)
    assert stale_plan.reason_by_member == (("raw-member", "raw_source_version_changed"),)
    assert stale_catalog.lookup("payment")[1].freshness == Freshness.STALE


def test_catalog_rejects_cross_profile_or_scope_entries(scope: KnowledgeScope) -> None:
    raw = source(1)
    other = KnowledgeScope("personal", (100,))
    with pytest.raises(KnowledgeCatalogError, match="exact profile scope"):
        KnowledgeCatalog(scope, wiki=(wiki(other, raw),))
    with pytest.raises(KnowledgeCatalogError, match="exceeds the catalog scope"):
        KnowledgeCatalog(scope, raw=(RawCatalogReference(SourceReference("tg://chat/999/message/1", 999, "message", "v1"), ("payment",)),))


def test_derived_and_evidence_citations_cannot_escape_exact_scope(scope: KnowledgeScope) -> None:
    outside = SourceReference("tg://chat/999/message/1", 999, "message", "v1")
    with pytest.raises(KnowledgeCatalogError, match="wiki assertion citations"):
        wiki(scope, outside)
    member = EvidenceSetMember("outside-member", EvidenceMemberKind.RAW, "message-999", (outside.citation,), "v1")
    with pytest.raises(KnowledgeCatalogError, match="evidence member citations"):
        EvidenceSetReference(
            "outside-set", scope, "purpose", "query", (member,), "summary", "2026-08-03T10:00:00Z", "set-v1", ("payment",)
        )


def test_evidence_set_freshness_uses_versions_and_wiki_missing_raw_provenance_is_not_accidentally_stale(scope: KnowledgeScope) -> None:
    raw = source(1)
    evidence = evidence_set(scope, raw)
    stale_evidence = KnowledgeCatalog(
        scope,
        raw=(RawCatalogReference(source(1, "v2"), ("other",)),),
        evidence_sets=(evidence,),
    ).lookup("payment")[0]
    wiki_without_current_raw = KnowledgeCatalog(scope, wiki=(wiki(scope, raw),)).lookup("payment")[0]

    assert stale_evidence.layer == KnowledgeLayer.EVIDENCE_SET
    assert stale_evidence.freshness == Freshness.STALE
    assert wiki_without_current_raw.freshness == Freshness.CURRENT


def test_research_session_is_compact_resumable_and_rejects_transcripts_secrets_and_paths(scope: KnowledgeScope) -> None:
    checkpoint = ResearchCheckpoint(
        summary="Found one cited payment decision.",
        decisions=("Use cited raw source before reporting.",),
        unresolved_questions=("Was the deadline later changed?",),
        evidence_set_ids=("payment-research",),
        created_at="2026-08-03T10:00:00Z",
    )
    session = ResearchSession(
        "session-payment",
        scope,
        "Verify the payment deadline",
        (("token_budget", 4_000), ("tool_calls", 2)),
        checkpoint,
        "2026-08-03T10:00:00Z",
        "2026-08-03T10:00:00Z",
    )
    resumed = session.with_checkpoint(checkpoint, updated_at="2026-08-03T11:00:00Z")

    assert resumed.resume_view()["checkpoint"]["evidence_set_ids"] == ["payment-research"]
    assert "hidden_reasoning" not in resumed.resume_view()["checkpoint"]
    with pytest.raises(KnowledgeCatalogError, match="must not store"):
        validate_session_metadata({"hidden_reasoning": "private chain"})
    with pytest.raises(KnowledgeCatalogError, match="credentials|session paths"):
        validate_session_metadata({"note": "api_key=secret"})
    with pytest.raises(KnowledgeCatalogError, match="must not store"):
        validate_session_metadata({"safe": [{"access_token": "secret"}]})
    with pytest.raises(KnowledgeCatalogError, match="private absolute path"):
        validate_session_metadata({"safe": ["C:\\Users\\private\\archive"]})
    with pytest.raises(KnowledgeCatalogError, match="private absolute path"):
        validate_session_metadata({"safe": {"note": "/var/private/archive"}})
    with pytest.raises(TypeError):
        ResearchCheckpoint(summary="ok", created_at="2026-08-03T10:00:00Z", full_transcript="not a field")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "metadata_factory",
    (
        lambda: {"apikey": "redacted"},
        lambda: {"apiKey": "redacted"},
        lambda: {"secret": "redacted"},
        lambda: {"client_secret": "redacted"},
        lambda: {"nested": [{"apiKey": "redacted"}]},
        lambda: {"note": "C:/private/archive"},
        lambda: {"note": "\\\\server\\share\\archive"},
        lambda: {"note": "file:///private/archive"},
    ),
    ids=(
        "apikey_key",
        "camel_case_api_key",
        "secret_key",
        "client_secret_key",
        "nested_api_key",
        "windows_forward_slash_path",
        "unc_path",
        "file_uri",
    ),
)
def test_session_metadata_rejects_obfuscated_secret_keys_and_private_path_forms(metadata_factory: object) -> None:
    with pytest.raises(KnowledgeCatalogError):
        validate_session_metadata(metadata_factory())  # type: ignore[operator]


def test_session_metadata_keeps_non_private_https_reference() -> None:
    validate_session_metadata({"source": "https://example.test/evidence"})


@pytest.mark.parametrize("value", [b"raw", bytearray(b"raw"), memoryview(b"raw")])
def test_session_metadata_rejects_binary_values_recursively(value: object) -> None:
    with pytest.raises(KnowledgeCatalogError, match="binary"):
        validate_session_metadata({"safe": [{"nested": value}]})


@pytest.mark.parametrize("value", [{"set"}, float("nan"), object()])
def test_session_metadata_rejects_non_json_values(value: object) -> None:
    with pytest.raises(KnowledgeCatalogError, match="JSON-compatible"):
        validate_session_metadata({"safe": value})


def test_bounded_expansion_uses_only_injected_authorized_resolver(scope: KnowledgeScope) -> None:
    requested = source(1)
    calls: list[tuple[KnowledgeScope, tuple[str, ...], int]] = []

    def resolver(received_scope: KnowledgeScope, citations: tuple[str, ...], limit: int) -> tuple[ExpandedSource, ...]:
        calls.append((received_scope, citations, limit))
        return (ExpandedSource(requested, "Original message context."),)

    expanded = expand_sources(scope, (requested.citation,), resolver, limit=1)

    assert expanded[0].source == requested
    assert calls == [(scope, (requested.citation,), 1)]
    outside = SourceReference("tg://chat/100/message/2", 100, "message", "v1")
    with pytest.raises(KnowledgeCatalogError, match="outside the authorized request"):
        expand_sources(scope, (requested.citation,), lambda *_args: (ExpandedSource(outside, "wrong"),), limit=1)


def test_catalog_hit_requires_complete_stable_contract() -> None:
    with pytest.raises(KnowledgeCatalogError, match="sorted, unique"):
        CatalogHit("raw-100-1", KnowledgeLayer.RAW, Authority.AUTHORITATIVE, 1.0, Freshness.CURRENT, "v1", ("tg://chat/100/message/1", "tg://chat/100/message/1"), None)


def test_immutable_times_are_canonical_and_evidence_members_have_stable_order(scope: KnowledgeScope) -> None:
    raw = source(1)
    members = (
        EvidenceSetMember("wiki-member", EvidenceMemberKind.WIKI, "project-a", (raw.citation,), "wiki-v1"),
        EvidenceSetMember("raw-member", EvidenceMemberKind.RAW, "message-1", (raw.citation,), "v1"),
    )
    evidence = EvidenceSetReference(
        "ordered-set", scope, "purpose", "query", members, "summary", "2026-08-03T13:00:00+03:00", "set-v1", ("payment",)
    )
    checkpoint = ResearchCheckpoint("summary", created_at="2026-08-03T13:00:00+03:00")
    session = ResearchSession(
        "offset-session", scope, "purpose", (("token_budget", 1),), checkpoint,
        "2026-08-03T13:00:00+03:00", "2026-08-03T13:00:00+03:00",
    )

    assert evidence.created_at == "2026-08-03T10:00:00Z"
    assert [member.member_id for member in evidence.members] == ["raw-member", "wiki-member"]
    assert evidence.as_json()["members"][0]["member_id"] == "raw-member"
    assert checkpoint.created_at == "2026-08-03T10:00:00Z"
    assert session.updated_at == "2026-08-03T10:00:00Z"
    assert session.with_checkpoint(checkpoint, updated_at="2026-08-03T10:01:00Z").updated_at == "2026-08-03T10:01:00Z"
    with pytest.raises(KnowledgeCatalogError, match="sorted, unique"):
        EvidenceSetMember("bad-order", EvidenceMemberKind.RAW, "message-1", ("tg://chat/100/message/2", "tg://chat/100/message/1"), "v1")
