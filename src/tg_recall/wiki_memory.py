"""Private, deterministic wiki-memory primitives with no archive integration.

The caller supplies an already-authorized profile-local directory, scope,
records, and (for expansion) a read-only resolver.  This module deliberately
does not open SQLite, Telegram, network clients, or application configuration.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


WIKI_MEMORY_SCHEMA_VERSION = 1
_CITATION_RE = re.compile(r"^tg://chat/(-?\d+)/message/(\d+)$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_KIND_RE = re.compile(r"^(message|transcript)$")


class WikiMemoryError(ValueError):
    """A caller supplied invalid, unauthorized, or unsafe wiki-memory input."""


class PageKind(StrEnum):
    PERSON = "people"
    RELATIONSHIP = "relationships"
    PROJECT = "projects"
    DECISION = "decisions"
    COMMUNICATION_STYLE = "communication-styles"


class AssertionKind(StrEnum):
    OBSERVED = "observed"
    HYPOTHESIS = "hypothesis"


class Freshness(StrEnum):
    CURRENT = "current"
    STALE = "stale"


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _hash_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{hashlib.sha256(_canonical_json(value)).hexdigest()[:24]}"


def _parse_timestamp(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise WikiMemoryError(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WikiMemoryError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise WikiMemoryError(f"{label} must include a timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _require_identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise WikiMemoryError(f"{label} must be a normalized logical identifier")
    return value


def _parse_citation(value: str) -> tuple[int, int]:
    match = _CITATION_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise WikiMemoryError("citation must be an exact tg://chat/.../message/... reference")
    return int(match.group(1)), int(match.group(2))


def _require_citation(value: str) -> str:
    _parse_citation(value)
    return value


@dataclass(frozen=True)
class AuthorizedWikiScope:
    """Explicit caller-approved boundary; no scope is inferred from a path."""

    profile_id: str
    scope_id: str
    chat_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.profile_id, "profile_id")
        _require_identifier(self.scope_id, "scope_id")
        if not self.chat_ids or len(set(self.chat_ids)) != len(self.chat_ids) or any(not isinstance(chat_id, int) for chat_id in self.chat_ids):
            raise WikiMemoryError("chat_ids must be a non-empty set of unique integer IDs")
        object.__setattr__(self, "chat_ids", tuple(sorted(self.chat_ids)))

    def as_json(self) -> dict[str, Any]:
        return {"chat_ids": sorted(self.chat_ids), "profile_id": self.profile_id, "scope_id": self.scope_id}


@dataclass(frozen=True)
class RawSourceRecord:
    """Authorized raw evidence, never fetched or normalized by this module."""

    citation: str
    chat_id: int
    timestamp: str
    text: str
    source_id: str
    source_kind: str = "message"
    source_version: str = "1"

    def __post_init__(self) -> None:
        _require_citation(self.citation)
        _require_identifier(self.source_id, "source_id")
        if not isinstance(self.chat_id, int):
            raise WikiMemoryError("chat_id must be an integer")
        citation_chat_id, _message_id = _parse_citation(self.citation)
        if citation_chat_id != self.chat_id:
            raise WikiMemoryError("citation chat ID must match record chat_id")
        _parse_timestamp(self.timestamp, "record timestamp")
        if not isinstance(self.text, str):
            raise WikiMemoryError("record text must be a string")
        if not isinstance(self.source_version, str) or not self.source_version:
            raise WikiMemoryError("source_version is required")
        if not _KIND_RE.fullmatch(self.source_kind):
            raise WikiMemoryError("source_kind must be message or transcript")

    @property
    def cursor(self) -> tuple[str, str, str, str]:
        return (_parse_timestamp(self.timestamp, "record timestamp"), self.citation, self.source_kind, self.source_id)

    def as_json(self) -> dict[str, Any]:
        return {
            "citation": self.citation,
            "chat_id": self.chat_id,
            "source_id": self.source_id,
            "source_kind": self.source_kind,
            "source_version": self.source_version,
            "text": self.text,
            "timestamp": _parse_timestamp(self.timestamp, "record timestamp"),
        }


@dataclass(frozen=True)
class WikiAssertion:
    """A derived claim whose supporting Telegram citations stay explicit."""

    text: str
    kind: AssertionKind
    confidence: float
    citations: tuple[str, ...]
    assertion_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise WikiMemoryError("assertion text is required")
        if not 0 <= self.confidence <= 1:
            raise WikiMemoryError("assertion confidence must be in [0, 1]")
        if not self.citations:
            raise WikiMemoryError("assertions require at least one citation")
        if tuple(sorted(set(self.citations))) != self.citations:
            raise WikiMemoryError("assertion citations must be sorted and unique")
        for citation in self.citations:
            _require_citation(citation)
        if self.assertion_id is not None:
            _require_identifier(self.assertion_id, "assertion_id")

    @property
    def stable_id(self) -> str:
        return self.assertion_id or _hash_id(
            "assertion",
            {"citations": list(self.citations), "kind": self.kind.value, "text": self.text},
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "assertion_id": self.stable_id,
            "citations": list(self.citations),
            "confidence": self.confidence,
            "kind": self.kind.value,
            "text": self.text,
        }


@dataclass(frozen=True)
class WikiPageDraft:
    kind: PageKind
    subject_id: str
    title: str
    assertions: tuple[WikiAssertion, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.subject_id, "subject_id")
        if not isinstance(self.title, str) or not self.title.strip():
            raise WikiMemoryError("page title is required")
        if not self.assertions:
            raise WikiMemoryError("page assertions are required")
        assertion_ids = [assertion.stable_id for assertion in self.assertions]
        if len(set(assertion_ids)) != len(assertion_ids):
            raise WikiMemoryError("page assertions must have unique IDs")

    @property
    def citations(self) -> tuple[str, ...]:
        return tuple(sorted({citation for assertion in self.assertions for citation in assertion.citations}))


@dataclass(frozen=True)
class SnapshotManifest:
    snapshot_id: str
    scope: AuthorizedWikiScope
    created_at: str
    source_cursor: tuple[str, str, str, str]
    record_count: int

    def __post_init__(self) -> None:
        _require_identifier(self.snapshot_id, "snapshot_id")
        _parse_timestamp(self.created_at, "snapshot created_at")
        if len(self.source_cursor) != 4:
            raise WikiMemoryError("source_cursor must contain timestamp, citation, kind, and source ID")
        _parse_timestamp(self.source_cursor[0], "source cursor timestamp")
        _require_citation(self.source_cursor[1])
        if not _KIND_RE.fullmatch(self.source_cursor[2]):
            raise WikiMemoryError("source cursor kind is invalid")
        _require_identifier(self.source_cursor[3], "source cursor source ID")
        if self.record_count < 1:
            raise WikiMemoryError("snapshot record_count must be positive")

    def as_json(self) -> dict[str, Any]:
        return {
            "created_at": _parse_timestamp(self.created_at, "snapshot created_at"),
            "record_count": self.record_count,
            "scope": self.scope.as_json(),
            "snapshot_id": self.snapshot_id,
            "source_cursor": list(self.source_cursor),
            "schema_version": WIKI_MEMORY_SCHEMA_VERSION,
        }


@dataclass(frozen=True)
class WikiPageRevision:
    revision_id: str
    draft: WikiPageDraft
    scope: AuthorizedWikiScope
    snapshot_id: str
    updated_at: str

    def __post_init__(self) -> None:
        _require_identifier(self.revision_id, "revision_id")
        _require_identifier(self.snapshot_id, "snapshot_id")
        _parse_timestamp(self.updated_at, "page updated_at")

    def as_json(self) -> dict[str, Any]:
        return {
            "assertions": [assertion.as_json() for assertion in self.draft.assertions],
            "kind": self.draft.kind.value,
            "revision_id": self.revision_id,
            "scope": self.scope.as_json(),
            "snapshot_id": self.snapshot_id,
            "subject_id": self.draft.subject_id,
            "title": self.draft.title,
            "updated_at": _parse_timestamp(self.updated_at, "page updated_at"),
        }


@dataclass(frozen=True)
class CompilationResult:
    snapshot: SnapshotManifest | None
    page_revisions: tuple[WikiPageRevision, ...]
    delta_count: int


@dataclass(frozen=True)
class WikiLookupHit:
    kind: PageKind
    subject_id: str
    title: str
    revision_id: str
    snapshot_id: str
    updated_at: str
    freshness: Freshness
    citations: tuple[str, ...]
    excerpt: str
    assertions: tuple[WikiAssertion, ...]

    def as_json(self) -> dict[str, Any]:
        return {
            "assertions": [assertion.as_json() for assertion in self.assertions],
            "citations": list(self.citations),
            "excerpt": self.excerpt,
            "freshness": self.freshness.value,
            "kind": self.kind.value,
            "revision_id": self.revision_id,
            "snapshot_id": self.snapshot_id,
            "subject_id": self.subject_id,
            "title": self.title,
            "updated_at": self.updated_at,
        }


@runtime_checkable
class AuthorizedSourceResolver(Protocol):
    """Read-only resolver injected by the archive/policy integration layer."""

    def __call__(self, scope: AuthorizedWikiScope, citations: tuple[str, ...], limit: int) -> Iterable[RawSourceRecord]: ...


def select_delta(
    records: Iterable[RawSourceRecord],
    scope: AuthorizedWikiScope,
    last_snapshot: SnapshotManifest | None,
    *,
    previous_records: Iterable[RawSourceRecord] = (),
) -> tuple[RawSourceRecord, ...]:
    """Select new or version-changed records from a complete scope snapshot.

    A changed Telegram source may retain its original message timestamp.  Its
    ``source_version`` must therefore invalidate derived pages even when its
    cursor is not newer.  If a committed source disappears from the supplied
    complete snapshot, this pure layer refuses to call old derived knowledge
    current; an integration must surface an explicit deletion/invalidation.
    """

    if last_snapshot is not None and last_snapshot.scope != scope:
        raise WikiMemoryError("last snapshot belongs to a different authorized scope")
    unique: dict[tuple[str, str, str], RawSourceRecord] = {}
    for record in records:
        if record.chat_id not in scope.chat_ids:
            raise WikiMemoryError("raw record is outside the authorized chat scope")
        key = (record.citation, record.source_kind, record.source_id)
        previous = unique.get(key)
        if previous is not None and previous.as_json() != record.as_json():
            raise WikiMemoryError("raw records contain conflicting versions for one source ID")
        unique[key] = record
    previous: dict[tuple[str, str, str], RawSourceRecord] = {}
    for record in previous_records:
        if record.chat_id not in scope.chat_ids:
            raise WikiMemoryError("previous raw record is outside the authorized chat scope")
        key = (record.citation, record.source_kind, record.source_id)
        prior = previous.get(key)
        if prior is not None and prior.as_json() != record.as_json():
            raise WikiMemoryError("committed snapshots contain conflicting versions for one source ID")
        previous[key] = record
    if previous:
        deleted = sorted(set(previous) - set(unique))
        if deleted:
            raise WikiMemoryError("current source snapshot omits committed records; explicit deletion invalidation is required")
    cursor = last_snapshot.source_cursor if last_snapshot else None
    return tuple(
        record
        for key, record in sorted(unique.items(), key=lambda item: item[1].cursor)
        if cursor is None
        or record.cursor > cursor
        or (key in previous and record.source_version != previous[key].source_version)
    )


class WikiMemoryStore:
    """Writes immutable artifacts only below one caller-owned private root."""

    def __init__(self, wiki_root: str | Path, *, replace_file: Callable[[Path, Path], None] | None = None):
        root = Path(wiki_root)
        if root.exists() and (root.is_symlink() or not root.is_dir()):
            raise WikiMemoryError("wiki root must be a real directory")
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink():
            raise WikiMemoryError("wiki root must not be a symbolic link")
        self._root = root.resolve()
        self._assert_safe_directory(self._root)
        self._replace_file = replace_file or os.replace

    @property
    def root(self) -> Path:
        return self._root

    def latest_snapshot(self, scope: AuthorizedWikiScope) -> SnapshotManifest | None:
        manifests = self._committed_manifests(scope)
        return max(manifests.values(), key=lambda item: (item.created_at, item.source_cursor, item.snapshot_id), default=None)

    def _committed_manifests(self, scope: AuthorizedWikiScope) -> dict[str, SnapshotManifest]:
        """Return only fully committed snapshots for this exact profile scope."""

        manifests: dict[str, SnapshotManifest] = {}
        directory = self._directory("raw", scope.profile_id, scope.scope_id)
        if not directory.exists():
            return manifests
        for path in directory.glob("*.manifest.json"):
            if path.is_symlink():
                raise WikiMemoryError("wiki manifest must not be a symbolic link")
            manifest = _manifest_from_json(json.loads(path.read_text(encoding="utf-8")))
            if manifest.scope == scope:
                self._load_snapshot_records(manifest)
                manifests[manifest.snapshot_id] = manifest
        return manifests

    def _load_snapshot_records(self, manifest: SnapshotManifest) -> tuple[RawSourceRecord, ...]:
        records_path = self._file(
            "raw", manifest.scope.profile_id, manifest.scope.scope_id, f"{manifest.snapshot_id}.jsonl"
        )
        if not records_path.is_file() or records_path.is_symlink():
            raise WikiMemoryError("snapshot records are missing or unsafe")
        try:
            records = tuple(RawSourceRecord(**json.loads(line)) for line in records_path.read_text(encoding="utf-8").splitlines() if line)
        except (json.JSONDecodeError, TypeError) as exc:
            raise WikiMemoryError("snapshot records are invalid") from exc
        if len(records) != manifest.record_count or not records:
            raise WikiMemoryError("snapshot record count does not match committed manifest")
        if tuple(sorted(records, key=lambda item: item.cursor)) != records or records[-1].cursor != manifest.source_cursor:
            raise WikiMemoryError("snapshot source cursor does not match committed records")
        if any(record.chat_id not in manifest.scope.chat_ids for record in records):
            raise WikiMemoryError("snapshot records exceed the committed chat scope")
        return records

    def compile(
        self,
        scope: AuthorizedWikiScope,
        records: Iterable[RawSourceRecord],
        pages: Iterable[WikiPageDraft],
        *,
        created_at: str,
    ) -> CompilationResult:
        """Stage a delta snapshot and immutable page revisions without overwrite."""

        created_at = _parse_timestamp(created_at, "compilation created_at")
        drafts = tuple(pages)
        draft_keys = [(draft.kind, draft.subject_id) for draft in drafts]
        if len(set(draft_keys)) != len(draft_keys):
            raise WikiMemoryError("one compilation cannot contain duplicate page drafts for one kind and subject")
        last_snapshot = self.latest_snapshot(scope)
        delta = select_delta(
            records,
            scope,
            last_snapshot,
            previous_records=self._committed_source_records(scope),
        )
        if not delta:
            return CompilationResult(None, (), 0)
        if last_snapshot is not None and created_at <= last_snapshot.created_at:
            raise WikiMemoryError("compilation created_at must be newer than the last successful snapshot")
        snapshot = self._make_snapshot(scope, delta, created_at)
        all_citations = self._known_citations(scope) | {record.citation for record in delta}
        revisions = tuple(self._make_revision(draft, snapshot, created_at, all_citations) for draft in drafts)
        targets: list[tuple[tuple[str, ...], bytes]] = [
            (("raw", scope.profile_id, scope.scope_id, f"{snapshot.snapshot_id}.jsonl"), _render_snapshot_records(delta)),
        ]
        for revision in revisions:
            targets.append(
                (
                    ("pages", scope.profile_id, scope.scope_id, revision.draft.kind.value, revision.draft.subject_id, f"{revision.revision_id}.md"),
                    _render_page(revision),
                )
            )
        # The manifest is the success marker consulted by delta selection, so
        # it is moved only after raw evidence and page revisions are in place.
        targets.append(
            (("raw", scope.profile_id, scope.scope_id, f"{snapshot.snapshot_id}.manifest.json"), _canonical_json(snapshot.as_json()) + b"\n")
        )
        self._stage_immutable_files(targets)
        return CompilationResult(snapshot, revisions, len(delta))

    def lookup(self, scope: AuthorizedWikiScope, query: str, *, limit: int = 8, excerpt_chars: int = 600) -> tuple[WikiLookupHit, ...]:
        """Return compact page revisions with stale/current metadata and citations."""

        if not isinstance(query, str) or not query.strip():
            raise WikiMemoryError("lookup query is required")
        if limit < 1 or excerpt_chars < 1:
            raise WikiMemoryError("lookup limits must be positive")
        latest = self.latest_snapshot(scope)
        if latest is None:
            return ()
        normalized_query = query.casefold()
        revisions = self._latest_page_revisions(scope)
        scored: list[tuple[int, WikiLookupHit]] = []
        for revision in revisions:
            rendered = _render_page(revision).decode("utf-8")
            score = rendered.casefold().count(normalized_query)
            if score == 0:
                continue
            freshness = Freshness.CURRENT if revision.snapshot_id == latest.snapshot_id else Freshness.STALE
            excerpt = _compact_excerpt(rendered, normalized_query, excerpt_chars)
            scored.append(
                (
                    score,
                    WikiLookupHit(
                        kind=revision.draft.kind,
                        subject_id=revision.draft.subject_id,
                        title=revision.draft.title,
                        revision_id=revision.revision_id,
                        snapshot_id=revision.snapshot_id,
                        updated_at=revision.updated_at,
                        freshness=freshness,
                        citations=revision.draft.citations,
                        excerpt=excerpt,
                        assertions=revision.draft.assertions,
                    ),
                )
            )
        return tuple(hit for _, hit in sorted(scored, key=lambda item: (-item[0], item[1].kind.value, item[1].subject_id))[:limit])

    def load_revisions(self, scope: AuthorizedWikiScope, revision_ids: Iterable[str]) -> tuple[WikiPageRevision, ...]:
        """Load exact committed revisions through profile-local logical paths.

        This public read API never accepts a page path.  It parses each selected
        Markdown revision, confirms its immutable ID against the structured
        assertions, and requires a committed snapshot in the same profile and
        scope.  Callers can safely derive export content without bypassing the
        wiki store's path, symlink, or commit checks.
        """

        requested = tuple(revision_ids)
        if not requested or len(set(requested)) != len(requested):
            raise WikiMemoryError("revision_ids must be non-empty and unique")
        for revision_id in requested:
            _require_identifier(revision_id, "revision_id")
        committed_snapshot_ids = set(self._committed_manifests(scope))
        root = self._directory("pages", scope.profile_id, scope.scope_id)
        if not root.exists():
            raise WikiMemoryError("selected wiki revisions are not committed for this scope")
        found: dict[str, WikiPageRevision] = {}
        for path in root.rglob("*.md"):
            self._assert_file_within(path)
            revision = _parse_page(path.read_text(encoding="utf-8"))
            if revision.scope != scope or revision.snapshot_id not in committed_snapshot_ids:
                continue
            if revision.revision_id not in requested:
                continue
            _verify_revision_identity(revision)
            if revision.revision_id in found:
                raise WikiMemoryError("duplicate committed wiki revision ID")
            found[revision.revision_id] = revision
        missing = sorted(set(requested) - set(found))
        if missing:
            raise WikiMemoryError(f"selected wiki revisions are missing or unsafe: {missing}")
        return tuple(found[revision_id] for revision_id in requested)

    @staticmethod
    def render_revision(revision: WikiPageRevision) -> str:
        """Render parsed, ID-verified derived content for an explicit export."""

        _verify_revision_identity(revision)
        return _render_page(revision).decode("utf-8")

    def expand_assertion(
        self,
        scope: AuthorizedWikiScope,
        assertion: WikiAssertion,
        resolver: AuthorizedSourceResolver,
        *,
        limit: int = 8,
    ) -> tuple[RawSourceRecord, ...]:
        """Ask an injected read-only resolver for only this assertion's citations."""

        if limit < 1:
            raise WikiMemoryError("expansion limit must be positive")
        records = tuple(resolver(scope, assertion.citations, limit))
        if len(records) > limit:
            raise WikiMemoryError("source resolver exceeded the requested expansion limit")
        requested = set(assertion.citations)
        for record in records:
            if record.chat_id not in scope.chat_ids or record.citation not in requested:
                raise WikiMemoryError("source resolver returned evidence outside the authorized assertion scope")
        return records

    def _make_snapshot(self, scope: AuthorizedWikiScope, delta: Sequence[RawSourceRecord], created_at: str) -> SnapshotManifest:
        cursor = delta[-1].cursor
        snapshot_id = _hash_id(
            "snapshot",
            {"records": [record.as_json() for record in delta], "scope": scope.as_json(), "source_cursor": list(cursor)},
        )
        return SnapshotManifest(snapshot_id, scope, created_at, cursor, len(delta))

    def _make_revision(
        self, draft: WikiPageDraft, snapshot: SnapshotManifest, updated_at: str, known_citations: set[str]
    ) -> WikiPageRevision:
        unknown = set(draft.citations) - known_citations
        if unknown:
            raise WikiMemoryError(f"page assertions cite evidence absent from authorized snapshots: {sorted(unknown)}")
        revision_id = _hash_id(
            "revision",
            {"draft": {"assertions": [item.as_json() for item in draft.assertions], "kind": draft.kind.value, "subject": draft.subject_id, "title": draft.title}, "snapshot_id": snapshot.snapshot_id},
        )
        return WikiPageRevision(revision_id, draft, snapshot.scope, snapshot.snapshot_id, updated_at)

    def _known_citations(self, scope: AuthorizedWikiScope) -> set[str]:
        citations: set[str] = set()
        for manifest in self._committed_manifests(scope).values():
            citations.update(record.citation for record in self._load_snapshot_records(manifest))
        return citations

    def _committed_source_records(self, scope: AuthorizedWikiScope) -> tuple[RawSourceRecord, ...]:
        """Return the latest committed version for each stable source identity."""

        current: dict[tuple[str, str, str], RawSourceRecord] = {}
        manifests = sorted(
            self._committed_manifests(scope).values(),
            key=lambda item: (item.created_at, item.source_cursor, item.snapshot_id),
        )
        for manifest in manifests:
            for record in self._load_snapshot_records(manifest):
                current[(record.citation, record.source_kind, record.source_id)] = record
        return tuple(current.values())

    def _latest_page_revisions(self, scope: AuthorizedWikiScope) -> tuple[WikiPageRevision, ...]:
        root = self._directory("pages", scope.profile_id, scope.scope_id)
        if not root.exists():
            return ()
        committed_snapshot_ids = set(self._committed_manifests(scope))
        latest: dict[tuple[PageKind, str], WikiPageRevision] = {}
        for path in root.rglob("*.md"):
            self._assert_file_within(path)
            revision = _parse_page(path.read_text(encoding="utf-8"))
            if revision.scope != scope or revision.snapshot_id not in committed_snapshot_ids:
                continue
            key = (revision.draft.kind, revision.draft.subject_id)
            current = latest.get(key)
            if current is None or (revision.updated_at, revision.revision_id) > (current.updated_at, current.revision_id):
                latest[key] = revision
        return tuple(latest.values())

    def _directory(self, *parts: str) -> Path:
        return self._safe_path(*parts)

    def _file(self, *parts: str) -> Path:
        return self._safe_path(*parts)

    def _safe_path(self, *parts: str) -> Path:
        if not parts or any(not isinstance(part, str) or not _IDENTIFIER_RE.fullmatch(part.removesuffix(".jsonl").removesuffix(".json").removesuffix(".md").removesuffix(".manifest")) for part in parts):
            raise WikiMemoryError("unsafe wiki logical path")
        path = self._root.joinpath(*parts)
        try:
            path.resolve(strict=False).relative_to(self._root)
        except ValueError as exc:
            raise WikiMemoryError("wiki path escapes caller-owned root") from exc
        for parent in (self._root, *path.parents):
            if parent == self._root.parent:
                break
            if parent.exists() and parent.is_symlink():
                raise WikiMemoryError("wiki path may not traverse a symbolic link")
        return path

    def _assert_safe_directory(self, directory: Path) -> None:
        if directory.is_symlink() or not directory.is_dir():
            raise WikiMemoryError("wiki directory is unsafe")

    def _assert_file_within(self, path: Path) -> None:
        try:
            path.resolve(strict=False).relative_to(self._root)
        except ValueError as exc:
            raise WikiMemoryError("wiki file escapes caller-owned root") from exc
        if path.is_symlink():
            raise WikiMemoryError("wiki files must not be symbolic links")
        for parent in path.parents:
            if parent == self._root.parent:
                break
            if parent.exists() and parent.is_symlink():
                raise WikiMemoryError("wiki path may not traverse a symbolic link")

    def _stage_immutable_files(self, targets: Sequence[tuple[tuple[str, ...], bytes]]) -> None:
        """Commit staged artifacts without exposing a partial compilation.

        A manifest is the final target.  If interruption leaves byte-identical
        raw/page orphans before that marker, a deterministic retry reuses them;
        conflicting or symlinked artifacts are never overwritten.
        """

        staged = self._root / f".wiki-stage-{uuid.uuid4().hex}"
        staged.mkdir(mode=0o700)
        try:
            destinations = [self._file(*parts) for parts, _ in targets]
            if len(set(destinations)) != len(destinations):
                raise WikiMemoryError("immutable wiki compilation has duplicate artifact targets")
            pending: list[tuple[Path, bytes]] = []
            for (parts, payload), destination in zip(targets, destinations, strict=True):
                if os.path.lexists(destination):
                    if destination.is_symlink() or not destination.is_file():
                        raise WikiMemoryError("immutable wiki artifact is unsafe")
                    if destination.read_bytes() != payload:
                        raise WikiMemoryError("immutable wiki artifact conflicts with a different payload")
                    continue
                relative = destination.relative_to(self._root)
                stage_file = staged / relative
                stage_file.parent.mkdir(parents=True, exist_ok=True)
                stage_file.write_bytes(payload)
                pending.append((destination, payload))
            for destination, payload in pending:
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.parent.is_symlink():
                    raise WikiMemoryError("immutable wiki target parent is unsafe")
                if os.path.lexists(destination):
                    if destination.is_symlink() or not destination.is_file() or destination.read_bytes() != payload:
                        raise WikiMemoryError("immutable wiki target changed during commit")
                    continue
                self._replace_file(staged / destination.relative_to(self._root), destination)
        finally:
            if staged.exists():
                shutil.rmtree(staged)


def _render_snapshot_records(records: Sequence[RawSourceRecord]) -> bytes:
    return b"".join(_canonical_json(record.as_json()) + b"\n" for record in records)


def _render_page(revision: WikiPageRevision) -> bytes:
    values = revision.as_json()
    assertions_json = json.dumps(values["assertions"], ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    citations_json = json.dumps(sorted(revision.draft.citations), ensure_ascii=False, separators=(",", ":"))
    observed = [assertion for assertion in revision.draft.assertions if assertion.kind == AssertionKind.OBSERVED]
    hypotheses = [assertion for assertion in revision.draft.assertions if assertion.kind == AssertionKind.HYPOTHESIS]
    lines = [
        "---",
        f"schema_version: {WIKI_MEMORY_SCHEMA_VERSION}",
        f"kind: {revision.draft.kind.value}",
        f"subject_id: {revision.draft.subject_id}",
        f"revision_id: {revision.revision_id}",
        f"snapshot_id: {revision.snapshot_id}",
        f"updated_at: {revision.updated_at}",
        f"scope_json: {json.dumps(revision.scope.as_json(), ensure_ascii=False, separators=(',', ':'), sort_keys=True)}",
        f"source_citations: {citations_json}",
        f"assertions_json: {assertions_json}",
        "---",
        "",
        f"# {revision.draft.title}",
        "",
        "## Observed",
    ]
    lines.extend(_render_assertion_line(assertion) for assertion in observed)
    lines.extend(("", "## Hypotheses"))
    lines.extend(_render_assertion_line(assertion) for assertion in hypotheses)
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _render_assertion_line(assertion: WikiAssertion) -> str:
    return f"- [{assertion.confidence:.2f}; {', '.join(assertion.citations)}] {assertion.text}"


def _parse_page(content: str) -> WikiPageRevision:
    if not content.startswith("---\n"):
        raise WikiMemoryError("wiki page is missing YAML frontmatter")
    try:
        frontmatter, _body = content[4:].split("\n---\n", 1)
    except ValueError as exc:
        raise WikiMemoryError("wiki page frontmatter is malformed") from exc
    values: dict[str, str] = {}
    for line in frontmatter.splitlines():
        key, separator, value = line.partition(": ")
        if not separator:
            raise WikiMemoryError("wiki page frontmatter is malformed")
        values[key] = value
    required = {"schema_version", "kind", "subject_id", "revision_id", "snapshot_id", "updated_at", "scope_json", "source_citations", "assertions_json"}
    if set(values) != required or values["schema_version"] != str(WIKI_MEMORY_SCHEMA_VERSION):
        raise WikiMemoryError("wiki page frontmatter is unsupported")
    try:
        kind = PageKind(values["kind"])
        assertions_data = json.loads(values["assertions_json"])
        citations = tuple(json.loads(values["source_citations"]))
        scope_data = json.loads(values["scope_json"])
        scope = AuthorizedWikiScope(scope_data["profile_id"], scope_data["scope_id"], tuple(scope_data["chat_ids"]))
    except (ValueError, json.JSONDecodeError) as exc:
        raise WikiMemoryError("wiki page frontmatter JSON is invalid") from exc
    assertions = tuple(
        WikiAssertion(
            text=item["text"],
            kind=AssertionKind(item["kind"]),
            confidence=item["confidence"],
            citations=tuple(item["citations"]),
            assertion_id=item["assertion_id"],
        )
        for item in assertions_data
    )
    title_match = re.search(r"^# (.+)$", content, flags=re.MULTILINE)
    if title_match is None:
        raise WikiMemoryError("wiki page title is missing")
    draft = WikiPageDraft(kind, values["subject_id"], title_match.group(1), assertions)
    if draft.citations != citations:
        raise WikiMemoryError("wiki page citation mapping does not match assertions")
    return WikiPageRevision(values["revision_id"], draft, scope, values["snapshot_id"], values["updated_at"])


def _verify_revision_identity(revision: WikiPageRevision) -> None:
    expected = _hash_id(
        "revision",
        {
            "draft": {
                "assertions": [item.as_json() for item in revision.draft.assertions],
                "kind": revision.draft.kind.value,
                "subject": revision.draft.subject_id,
                "title": revision.draft.title,
            },
            "snapshot_id": revision.snapshot_id,
        },
    )
    if revision.revision_id != expected:
        raise WikiMemoryError("wiki revision content does not match its immutable revision ID")


def _manifest_from_json(value: Mapping[str, Any]) -> SnapshotManifest:
    try:
        if value["schema_version"] != WIKI_MEMORY_SCHEMA_VERSION:
            raise WikiMemoryError("unsupported snapshot schema version")
        scope_value = value["scope"]
        scope = AuthorizedWikiScope(scope_value["profile_id"], scope_value["scope_id"], tuple(scope_value["chat_ids"]))
        if set(value) != {"created_at", "record_count", "scope", "schema_version", "snapshot_id", "source_cursor"}:
            raise WikiMemoryError("snapshot manifest fields are invalid")
        return SnapshotManifest(value["snapshot_id"], scope, value["created_at"], tuple(value["source_cursor"]), value["record_count"])
    except (KeyError, TypeError) as exc:
        raise WikiMemoryError("snapshot manifest is invalid") from exc


def _compact_excerpt(content: str, query: str, limit: int) -> str:
    index = content.casefold().find(query)
    if index < 0:
        return content[:limit]
    start = max(0, index - limit // 4)
    return content[start : start + limit]
