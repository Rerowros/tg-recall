"""Pure-domain contract for profile-isolated, evidence-backed knowledge.

The module has no database, filesystem, Telegram, CLI, MCP, or provider
dependency.  Adapters supply already-authorized references and may persist the
immutable values below a profile according to their own privacy policy.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


KNOWLEDGE_CATALOG_SCHEMA_VERSION = 1
_CITATION_RE = re.compile(r"^tg://chat/(-?\d+)/message/(\d+)$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_FORBIDDEN_SESSION_KEYS = frozenset(
    {
        "credential",
        "credentials",
        "hidden_reasoning",
        "reasoning",
        "session_path",
        "telegram_session",
        "transcript",
        "codex_transcript",
        "full_transcript",
    }
)
_SECRET_OR_PATH_RE = re.compile(r"(?i)(api[_ -]?key|authorization:|password=|\.session\b|[a-z]:\\|/(?:home|users|private)/)")


class KnowledgeCatalogError(ValueError):
    """Invalid, cross-profile, or unsafe knowledge-domain input."""


class KnowledgeLayer(StrEnum):
    RAW = "raw"
    WIKI = "wiki"
    EVIDENCE_SET = "evidence_set"


class Authority(StrEnum):
    AUTHORITATIVE = "authoritative"
    DERIVED = "derived"
    NAVIGATION = "navigation"


class Freshness(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    CONTRADICTED = "contradicted"


class EvidenceMemberKind(StrEnum):
    RAW = "raw"
    WIKI = "wiki"


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise KnowledgeCatalogError(f"{label} must be a normalized logical identifier")
    return value


def _timestamp(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise KnowledgeCatalogError(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KnowledgeCatalogError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise KnowledgeCatalogError(f"{label} must include a timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _citation(value: str) -> tuple[int, int]:
    match = _CITATION_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise KnowledgeCatalogError("citation must be an exact tg://chat/.../message/... reference")
    return int(match.group(1)), int(match.group(2))


def _normalized_terms(values: Iterable[str], label: str = "topics") -> tuple[str, ...]:
    result = tuple(sorted({value.casefold().strip() for value in values if isinstance(value, str) and value.strip()}))
    if not result:
        raise KnowledgeCatalogError(f"{label} must contain at least one non-empty term")
    return result


def _safe_compact_text(value: str, label: str, *, maximum: int = 2_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise KnowledgeCatalogError(f"{label} must be a bounded non-empty string")
    if _SECRET_OR_PATH_RE.search(value):
        raise KnowledgeCatalogError(f"{label} must not contain credentials, session paths, or private absolute paths")
    return value.strip()


def _forbidden_session_key(value: Any) -> bool:
    key = str(value).casefold()
    normalized = re.findall(r"[a-z0-9]+", key)
    tokens = set(normalized)
    compact = "".join(normalized)
    if key in _FORBIDDEN_SESSION_KEYS:
        return True
    if compact in {"apikey", "accesstoken", "bearertoken", "clientsecret", "secret"}:
        return True
    if tokens & {"credential", "credentials", "password", "authorization", "auth", "session", "token", "secret"}:
        return True
    if "api" in tokens and ("key" in tokens or "token" in tokens):
        return True
    return ("hidden" in tokens and "reasoning" in tokens) or ("full" in tokens and "transcript" in tokens) or ("codex" in tokens and "transcript" in tokens) or ("session" in tokens and "path" in tokens)


def _validate_session_string(value: str, label: str) -> None:
    _safe_compact_text(value, label)
    if re.match(r"(?i)^(?:[a-z]:[\\/]|\\\\|//|file:|/)", value.strip()):
        raise KnowledgeCatalogError(f"{label} must not contain a private absolute path")


@dataclass(frozen=True)
class KnowledgeScope:
    """Explicit profile and chat boundary; raw content is never inferred."""

    profile_id: str
    chat_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        _identifier(self.profile_id, "profile_id")
        if not self.chat_ids or len(set(self.chat_ids)) != len(self.chat_ids) or any(not isinstance(item, int) for item in self.chat_ids):
            raise KnowledgeCatalogError("chat_ids must be a non-empty set of unique integer IDs")
        object.__setattr__(self, "chat_ids", tuple(sorted(self.chat_ids)))

    def as_json(self) -> dict[str, Any]:
        return {"chat_ids": list(self.chat_ids), "profile_id": self.profile_id}


@dataclass(frozen=True)
class SourceReference:
    """Logical source identity only; it deliberately does not carry raw text."""

    citation: str
    chat_id: int
    source_type: str
    source_version: str

    def __post_init__(self) -> None:
        citation_chat_id, _message_id = _citation(self.citation)
        if not isinstance(self.chat_id, int) or citation_chat_id != self.chat_id:
            raise KnowledgeCatalogError("source citation chat ID must match chat_id")
        if self.source_type not in {"message", "transcript"}:
            raise KnowledgeCatalogError("source_type must be message or transcript")
        if not isinstance(self.source_version, str) or not self.source_version:
            raise KnowledgeCatalogError("source_version is required")

    def as_json(self) -> dict[str, str | int]:
        return {
            "citation": self.citation,
            "chat_id": self.chat_id,
            "source_type": self.source_type,
            "source_version": self.source_version,
        }


@dataclass(frozen=True)
class RawCatalogReference:
    source: SourceReference
    topics: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "topics", _normalized_terms(self.topics))


@dataclass(frozen=True)
class WikiAssertionReference:
    """Compact derived statement with source versions, never copied raw rows."""

    assertion_id: str
    page_id: str
    scope: KnowledgeScope
    text: str
    confidence: float
    citations: tuple[str, ...]
    source_versions: tuple[tuple[str, str], ...]
    wiki_revision: str
    topics: tuple[str, ...]
    contradicted_by: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.assertion_id, "assertion_id")
        _identifier(self.page_id, "page_id")
        _identifier(self.wiki_revision, "wiki_revision")
        _safe_compact_text(self.text, "wiki assertion text")
        if not 0 <= self.confidence <= 1:
            raise KnowledgeCatalogError("wiki assertion confidence must be in [0, 1]")
        if tuple(sorted(set(self.citations))) != self.citations or not self.citations:
            raise KnowledgeCatalogError("wiki assertion citations must be sorted, unique, and non-empty")
        for item in self.citations:
            chat_id, _message_id = _citation(item)
            if chat_id not in self.scope.chat_ids:
                raise KnowledgeCatalogError("wiki assertion citations must stay inside the exact knowledge scope")
        versions = tuple(sorted(self.source_versions))
        if versions != self.source_versions or {item[0] for item in versions} != set(self.citations) or any(not version for _, version in versions):
            raise KnowledgeCatalogError("source_versions must exactly cover assertion citations")
        if tuple(sorted(set(self.contradicted_by))) != self.contradicted_by:
            raise KnowledgeCatalogError("contradicted_by citations must be sorted and unique")
        for item in self.contradicted_by:
            chat_id, _message_id = _citation(item)
            if chat_id not in self.scope.chat_ids:
                raise KnowledgeCatalogError("wiki assertion contradictions must stay inside the exact knowledge scope")
        object.__setattr__(self, "topics", _normalized_terms(self.topics))


@dataclass(frozen=True)
class EvidenceSetMember:
    member_id: str
    kind: EvidenceMemberKind
    logical_id: str
    citations: tuple[str, ...]
    version: str

    def __post_init__(self) -> None:
        _identifier(self.member_id, "evidence member_id")
        _identifier(self.logical_id, "evidence member logical_id")
        if tuple(sorted(set(self.citations))) != self.citations or not self.citations:
            raise KnowledgeCatalogError("evidence member citations must be sorted, unique, and non-empty")
        for item in self.citations:
            _citation(item)
        if not isinstance(self.version, str) or not self.version:
            raise KnowledgeCatalogError("evidence member version is required")

    def as_json(self) -> dict[str, Any]:
        return {
            "citations": list(self.citations),
            "kind": self.kind.value,
            "logical_id": self.logical_id,
            "member_id": self.member_id,
            "version": self.version,
        }


@dataclass(frozen=True)
class EvidenceSetReference:
    evidence_set_id: str
    scope: KnowledgeScope
    purpose: str
    query: str
    members: tuple[EvidenceSetMember, ...]
    summary: str
    created_at: str
    revision: str
    topics: tuple[str, ...]

    def __post_init__(self) -> None:
        _identifier(self.evidence_set_id, "evidence_set_id")
        _identifier(self.revision, "evidence_set revision")
        _safe_compact_text(self.purpose, "evidence set purpose", maximum=500)
        _safe_compact_text(self.query, "evidence set query", maximum=500)
        _safe_compact_text(self.summary, "evidence set summary")
        _timestamp(self.created_at, "evidence set created_at")
        if not self.members or len({item.member_id for item in self.members}) != len(self.members):
            raise KnowledgeCatalogError("evidence sets require uniquely identified members")
        for member in self.members:
            for citation in member.citations:
                chat_id, _message_id = _citation(citation)
                if chat_id not in self.scope.chat_ids:
                    raise KnowledgeCatalogError("evidence member citations must stay inside the exact knowledge scope")
        object.__setattr__(self, "members", tuple(sorted(self.members, key=lambda item: item.member_id)))
        object.__setattr__(self, "topics", _normalized_terms(self.topics))
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "evidence set created_at"))

    def as_json(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "evidence_set_id": self.evidence_set_id,
            "members": [member.as_json() for member in self.members],
            "purpose": self.purpose,
            "query": self.query,
            "revision": self.revision,
            "scope": self.scope.as_json(),
            "summary": self.summary,
            "topics": list(self.topics),
        }


@dataclass(frozen=True)
class VersionMap:
    """Adapter-supplied current raw and wiki versions; no persistence here."""

    raw: tuple[tuple[str, str], ...] = ()
    wiki: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        for values, label in ((self.raw, "raw"), (self.wiki, "wiki")):
            if tuple(sorted(values)) != values or len({item[0] for item in values}) != len(values):
                raise KnowledgeCatalogError(f"{label} version map must be sorted and have unique IDs")
            for logical_id, version in values:
                if not isinstance(logical_id, str) or not logical_id or not isinstance(version, str) or not version:
                    raise KnowledgeCatalogError(f"{label} version map entries require IDs and versions")

    def raw_version(self, citation: str) -> str | None:
        return dict(self.raw).get(citation)

    def wiki_version(self, page_id: str) -> str | None:
        return dict(self.wiki).get(page_id)


@dataclass(frozen=True)
class EvidenceReusePlan:
    evidence_set_id: str
    reusable: bool
    stale_member_ids: tuple[str, ...]
    refresh_member_ids: tuple[str, ...]
    reason_by_member: tuple[tuple[str, str], ...]

    def as_json(self) -> dict[str, Any]:
        return {
            "evidence_set_id": self.evidence_set_id,
            "reason_by_member": {key: value for key, value in self.reason_by_member},
            "refresh_member_ids": list(self.refresh_member_ids),
            "reusable": self.reusable,
            "stale_member_ids": list(self.stale_member_ids),
        }


def plan_evidence_reuse(evidence_set: EvidenceSetReference, versions: VersionMap) -> EvidenceReusePlan:
    """Identify only stale members; current evidence can be reused unchanged."""

    stale: list[str] = []
    reasons: list[tuple[str, str]] = []
    for member in evidence_set.members:
        if member.kind == EvidenceMemberKind.RAW:
            current = {versions.raw_version(citation) for citation in member.citations}
            reason = "raw_source_missing" if None in current else "raw_source_version_changed" if current != {member.version} else None
        else:
            current = versions.wiki_version(member.logical_id)
            reason = "wiki_revision_missing" if current is None else "wiki_revision_changed" if current != member.version else None
        if reason is not None:
            stale.append(member.member_id)
            reasons.append((member.member_id, reason))
    return EvidenceReusePlan(
        evidence_set_id=evidence_set.evidence_set_id,
        reusable=not stale,
        stale_member_ids=tuple(stale),
        refresh_member_ids=tuple(stale),
        reason_by_member=tuple(reasons),
    )


@dataclass(frozen=True)
class CatalogHit:
    logical_id: str
    layer: KnowledgeLayer
    authority: Authority
    confidence: float
    freshness: Freshness
    source_version: str
    citations: tuple[str, ...]
    summary: str | None

    def __post_init__(self) -> None:
        _identifier(self.logical_id, "catalog logical_id")
        if not 0 <= self.confidence <= 1:
            raise KnowledgeCatalogError("catalog confidence must be in [0, 1]")
        if not self.source_version:
            raise KnowledgeCatalogError("catalog source_version is required")
        if tuple(sorted(set(self.citations))) != self.citations or not self.citations:
            raise KnowledgeCatalogError("catalog citations must be sorted, unique, and non-empty")
        for citation in self.citations:
            _citation(citation)
        if self.summary is not None:
            _safe_compact_text(self.summary, "catalog summary")

    def as_json(self) -> dict[str, Any]:
        return {
            "authority": self.authority.value,
            "citations": list(self.citations),
            "confidence": self.confidence,
            "freshness": self.freshness.value,
            "layer": self.layer.value,
            "logical_id": self.logical_id,
            "source_version": self.source_version,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class KnowledgeCatalog:
    """Immutable profile-local view over references, not a fourth content copy."""

    scope: KnowledgeScope
    raw: tuple[RawCatalogReference, ...] = ()
    wiki: tuple[WikiAssertionReference, ...] = ()
    evidence_sets: tuple[EvidenceSetReference, ...] = ()
    current_versions: VersionMap | None = None

    def __post_init__(self) -> None:
        if any(item.source.chat_id not in self.scope.chat_ids for item in self.raw):
            raise KnowledgeCatalogError("raw catalog reference exceeds the catalog scope")
        if any(item.scope != self.scope for item in self.wiki) or any(item.scope != self.scope for item in self.evidence_sets):
            raise KnowledgeCatalogError("catalog entries must belong to the exact profile scope")
        identifiers = [item.source.citation for item in self.raw]
        if len(set(identifiers)) != len(identifiers):
            raise KnowledgeCatalogError("raw catalog citations must be unique")
        if len({item.assertion_id for item in self.wiki}) != len(self.wiki):
            raise KnowledgeCatalogError("wiki assertion IDs must be unique")
        if len({item.evidence_set_id for item in self.evidence_sets}) != len(self.evidence_sets):
            raise KnowledgeCatalogError("evidence set IDs must be unique")
        page_versions: dict[str, str] = {}
        for item in self.wiki:
            previous = page_versions.setdefault(item.page_id, item.wiki_revision)
            if previous != item.wiki_revision:
                raise KnowledgeCatalogError("one catalog page_id must have one current wiki revision")

    def lookup(self, query: str, *, limit: int = 8) -> tuple[CatalogHit, ...]:
        if not isinstance(query, str) or not query.strip() or limit < 1:
            raise KnowledgeCatalogError("query and limit are required")
        terms = _normalized_terms(query.split(), "query terms")
        versions = self.current_versions or VersionMap(
            raw=tuple(sorted((item.source.citation, item.source.source_version) for item in self.raw)),
            wiki=tuple(sorted({(item.page_id, item.wiki_revision) for item in self.wiki})),
        )
        raw_versions = dict(versions.raw)
        hits: list[tuple[int, int, CatalogHit]] = []
        for item in self.raw:
            score = _topic_score(terms, item.topics)
            if score:
                hits.append((score, 0, CatalogHit(
                    logical_id=f"raw-{item.source.chat_id}-{_citation(item.source.citation)[1]}", layer=KnowledgeLayer.RAW, authority=Authority.AUTHORITATIVE,
                    confidence=1.0, freshness=Freshness.CURRENT, source_version=item.source.source_version,
                    citations=(item.source.citation,), summary=None,
                )))
        for item in self.wiki:
            score = _topic_score(terms, item.topics) + _text_score(terms, item.text)
            if score:
                freshness = _wiki_freshness(item, raw_versions)
                hits.append((score, 1, CatalogHit(
                    logical_id=f"wiki-{item.assertion_id}", layer=KnowledgeLayer.WIKI, authority=Authority.DERIVED,
                    confidence=item.confidence, freshness=freshness, source_version=item.wiki_revision,
                    citations=item.citations, summary=item.text,
                )))
        for item in self.evidence_sets:
            score = _topic_score(terms, item.topics) + _text_score(terms, item.summary)
            if score:
                reuse = plan_evidence_reuse(item, versions)
                hits.append((score, 2, CatalogHit(
                    logical_id=f"evidence-set-{item.evidence_set_id}", layer=KnowledgeLayer.EVIDENCE_SET, authority=Authority.NAVIGATION,
                    confidence=0.5, freshness=Freshness.CURRENT if reuse.reusable else Freshness.STALE, source_version=item.revision,
                    citations=tuple(sorted({citation for member in item.members for citation in member.citations})), summary=item.summary,
                )))
        # Authority is not a relevance tiebreaker: an authoritative raw hit
        # must appear before related derived/navigation material so a conflict
        # cannot be presented as a summary-first conclusion.
        return tuple(hit for _, _, hit in sorted(hits, key=lambda item: (item[1], -item[0], item[2].logical_id))[:limit])

    def as_json(self) -> dict[str, Any]:
        return {
            "schema_version": KNOWLEDGE_CATALOG_SCHEMA_VERSION,
            "scope": self.scope.as_json(),
            "raw_count": len(self.raw),
            "wiki_count": len(self.wiki),
            "evidence_set_count": len(self.evidence_sets),
        }


def _topic_score(query_terms: tuple[str, ...], topics: tuple[str, ...]) -> int:
    return sum(2 for term in query_terms if term in topics)


def _text_score(query_terms: tuple[str, ...], text: str) -> int:
    normalized = text.casefold()
    return sum(1 for term in query_terms if term in normalized)


def _wiki_freshness(item: WikiAssertionReference, raw_versions: Mapping[str, str]) -> Freshness:
    if item.contradicted_by:
        return Freshness.CONTRADICTED
    for citation, recorded_version in item.source_versions:
        # Missing raw material is not evidence that a page became stale.  Only
        # a supplied current-version provenance can invalidate this revision.
        current_version = raw_versions.get(citation)
        if current_version is not None and current_version != recorded_version:
            return Freshness.STALE
    return Freshness.CURRENT


@dataclass(frozen=True)
class ResearchCheckpoint:
    """Bounded derived state; hidden reasoning/transcripts have no field here."""

    summary: str
    decisions: tuple[str, ...] = ()
    unresolved_questions: tuple[str, ...] = ()
    evidence_set_ids: tuple[str, ...] = ()
    created_at: str = ""

    def __post_init__(self) -> None:
        _safe_compact_text(self.summary, "checkpoint summary")
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "checkpoint created_at"))
        if tuple(sorted(set(self.evidence_set_ids))) != self.evidence_set_ids:
            raise KnowledgeCatalogError("checkpoint evidence_set_ids must be sorted and unique")
        for value in self.evidence_set_ids:
            _identifier(value, "checkpoint evidence_set_id")
        for value in (*self.decisions, *self.unresolved_questions):
            _safe_compact_text(value, "checkpoint decision or unresolved question", maximum=1_000)

    def as_json(self) -> dict[str, Any]:
        return {
            "created_at": _timestamp(self.created_at, "checkpoint created_at"),
            "decisions": list(self.decisions),
            "evidence_set_ids": list(self.evidence_set_ids),
            "summary": self.summary,
            "unresolved_questions": list(self.unresolved_questions),
        }


@dataclass(frozen=True)
class ResearchSession:
    session_id: str
    scope: KnowledgeScope
    purpose: str
    budgets: tuple[tuple[str, int], ...]
    checkpoint: ResearchCheckpoint
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        _identifier(self.session_id, "research session_id")
        _safe_compact_text(self.purpose, "research session purpose", maximum=500)
        if tuple(sorted(self.budgets)) != self.budgets or not self.budgets or len({key for key, _ in self.budgets}) != len(self.budgets) or any(not key or not isinstance(value, int) or value < 0 for key, value in self.budgets):
            raise KnowledgeCatalogError("research session budgets must be sorted non-negative integer pairs")
        created, updated = _timestamp(self.created_at, "research session created_at"), _timestamp(self.updated_at, "research session updated_at")
        if updated < created:
            raise KnowledgeCatalogError("research session updated_at must not precede created_at")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)

    def with_checkpoint(self, checkpoint: ResearchCheckpoint, *, updated_at: str) -> "ResearchSession":
        updated_at = _timestamp(updated_at, "research session updated_at")
        if updated_at < self.updated_at:
            raise KnowledgeCatalogError("research session checkpoints must be monotonic")
        return ResearchSession(self.session_id, self.scope, self.purpose, self.budgets, checkpoint, self.created_at, updated_at)

    def resume_view(self) -> dict[str, Any]:
        return {
            "budgets": {key: value for key, value in self.budgets},
            "checkpoint": self.checkpoint.as_json(),
            "created_at": self.created_at,
            "purpose": self.purpose,
            "scope": self.scope.as_json(),
            "session_id": self.session_id,
            "updated_at": self.updated_at,
        }


def validate_session_metadata(metadata: Mapping[str, Any]) -> None:
    """Reject untrusted fields that would turn a compact session into a transcript vault."""

    if not isinstance(metadata, Mapping):
        raise KnowledgeCatalogError("research session metadata must be a JSON object")
    _validate_session_value(metadata, "research session metadata")
    try:
        json.dumps(metadata, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise KnowledgeCatalogError("research session metadata must contain only JSON-compatible values") from exc


def _validate_session_value(value: Any, label: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise KnowledgeCatalogError("research session metadata keys must be strings")
            if _forbidden_session_key(key):
                raise KnowledgeCatalogError("research sessions must not store hidden reasoning, transcripts, credentials, or session paths")
            _validate_session_value(child, f"{label} {key}")
        return
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise KnowledgeCatalogError("research session metadata must not store binary values")
    if isinstance(value, Sequence) and not isinstance(value, str):
        for index, child in enumerate(value):
            _validate_session_value(child, f"{label}[{index}]")
        return
    if isinstance(value, str):
        _validate_session_string(value, label)
        return
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    raise KnowledgeCatalogError("research session metadata must contain only JSON-compatible values")


@dataclass(frozen=True)
class ExpandedSource:
    """Ephemeral resolver result; callers decide whether policy permits display."""

    source: SourceReference
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise KnowledgeCatalogError("expanded source text must be a string")


@runtime_checkable
class AuthorizedSourceResolver(Protocol):
    def __call__(self, scope: KnowledgeScope, citations: tuple[str, ...], limit: int) -> Iterable[ExpandedSource]: ...


def expand_sources(
    scope: KnowledgeScope,
    citations: Sequence[str],
    resolver: AuthorizedSourceResolver,
    *,
    limit: int = 8,
) -> tuple[ExpandedSource, ...]:
    """Expand only selected citations through a caller-injected read-only resolver."""

    requested = tuple(sorted(set(citations)))
    if not requested or len(requested) > limit or limit < 1:
        raise KnowledgeCatalogError("source expansion requires 1..limit explicit citations")
    for citation in requested:
        chat_id, _message_id = _citation(citation)
        if chat_id not in scope.chat_ids:
            raise KnowledgeCatalogError("source expansion citation exceeds the authorized chat scope")
    result = tuple(resolver(scope, requested, limit))
    if len(result) > limit:
        raise KnowledgeCatalogError("source resolver exceeded expansion limit")
    for item in result:
        if item.source.citation not in requested or item.source.chat_id not in scope.chat_ids:
            raise KnowledgeCatalogError("source resolver returned evidence outside the authorized request")
    return result
