"""Vendor-neutral, policy-gated synthesis over already retrieved evidence.

This module intentionally has no database, filesystem, Telegram, credential, or
network implementation.  An adapter receives a small immutable request and may
only return text, evidence identifiers, and usage metadata.  The caller keeps
retrieval and any external transport outside this boundary.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence


_SERIALIZED_DATA_CLASSES = frozenset({"message_text", "metadata"})


class ProviderFailure(RuntimeError):
    """A sanitized provider failure that is safe to expose as a status class."""

    def __init__(self, failure_class: str, message: str = "provider request failed") -> None:
        self.failure_class = _safe_failure_class(failure_class)
        super().__init__(message)


class SynthesisPolicyDenied(PermissionError):
    """Raised before request construction when external synthesis is not allowed."""


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class ProviderRequest:
    provider: str
    model: str
    question: str
    evidence: tuple["ProviderEvidence", ...]
    token_budget: int


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    evidence_ids: tuple[str, ...]
    usage: ProviderUsage = field(default_factory=ProviderUsage)


class SynthesisProvider(Protocol):
    """Adapter protocol; implementations must not receive archive capabilities."""

    def answer(self, request: ProviderRequest) -> ProviderResponse: ...


@dataclass(frozen=True)
class ProviderEvidence:
    """Whitelisted evidence safe to serialize to an explicitly allowed provider."""

    evidence_id: str
    citation: str
    timestamp: str
    chat_title: str
    text: str


@dataclass(frozen=True)
class SynthesisPolicy:
    """Explicit permissions required for a single synthesis attempt.

    ``credentials_configured`` is deliberately a boolean rather than a secret.
    It allows policy validation without propagating credentials through this API.
    """

    provider: str = "extractive"
    model: str | None = None
    credentials_configured: bool = False
    external_llm_enabled: bool = False
    ai_access_enabled: bool = False
    allowed_chat_ids: tuple[int, ...] = ()
    effective_since: str | None = None
    effective_until: str | None = None
    effective_media_policy: str | None = None
    allowed_data_classes: frozenset[str] = frozenset()
    max_evidence_items: int = 5
    token_budget: int = 12_000


@dataclass(frozen=True)
class SynthesisAudit:
    provider: str
    model: str | None
    policy_allowed: bool
    evidence_ids: tuple[str, ...]
    evidence_count: int
    token_budget: int
    usage: ProviderUsage | None
    latency_ms: int
    provider_status: str
    failure_class: str | None = None
    effective_chat_ids: tuple[int, ...] = ()
    effective_since: str | None = None
    effective_until: str | None = None
    effective_media_policy: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return only bounded metadata; prompts and credentials never enter it."""

        data = asdict(self)
        if self.usage is not None:
            data["usage"] = asdict(self.usage)
        return data


@dataclass(frozen=True)
class SynthesisAnswer:
    """Additive JSON contract usable alongside the existing extractive string."""

    answer: str
    citations: tuple[str, ...]
    mode: str
    provider_status: str
    provider: str | None = None
    model: str | None = None
    evidence_ids: tuple[str, ...] = ()
    usage: ProviderUsage | None = None
    failure_class: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "citations": list(self.citations),
            "synthesis": {
                "mode": self.mode,
                "provider_status": self.provider_status,
                "provider": self.provider,
                "model": self.model,
                "evidence_ids": list(self.evidence_ids),
                "failure_class": self.failure_class,
                "usage": asdict(self.usage) if self.usage is not None else None,
            },
        }


def policy_from_config(
    config: Any,
    *,
    scope_chat_ids: Iterable[int],
    allowed_data_classes: Iterable[str] | None = None,
    max_evidence_items: int = 5,
    token_budget: int = 12_000,
    effective_since: str | None = None,
    effective_until: str | None = None,
    effective_media_policy: str | None = None,
) -> SynthesisPolicy:
    """Adapt existing config objects without exposing a credential in the policy.

    Callers must pass the already-resolved, explicit scope rather than infer an
    archive-wide scope from config.  This makes an omitted scope a hard denial.
    """

    llm = getattr(config, "llm", None)
    provider_policy = getattr(config, "provider_policy", None)
    ai_access = getattr(config, "ai_access", None)
    api_key = getattr(llm, "api_key", None)
    requested_scope = tuple(dict.fromkeys(int(chat_id) for chat_id in scope_chat_ids))
    configured_scope = tuple(getattr(ai_access, "allowed_chat_ids", ()) or ())
    # A partial policy intersection is not a valid synthetic-answer scope: it
    # could silently turn a multi-chat request into a different question.
    exact_scope = requested_scope if requested_scope and set(requested_scope) <= set(configured_scope) else ()
    data_classes = (
        allowed_data_classes
        if allowed_data_classes is not None
        else getattr(provider_policy, "external_llm_data_classes", ())
    )
    return SynthesisPolicy(
        provider=getattr(llm, "provider", "extractive"),
        model=getattr(llm, "model", None),
        credentials_configured=bool(api_key),
        external_llm_enabled=bool(getattr(provider_policy, "external_llm_enabled", False)),
        ai_access_enabled=bool(getattr(ai_access, "enabled", False)),
        allowed_chat_ids=exact_scope,
        effective_since=effective_since,
        effective_until=effective_until,
        effective_media_policy=effective_media_policy,
        allowed_data_classes=frozenset(data_classes),
        max_evidence_items=max_evidence_items,
        token_budget=token_budget,
    )


def require_synthesis_policy(policy: SynthesisPolicy) -> None:
    """Validate permissions before serializing evidence or touching a provider."""

    if policy.provider in {"", "extractive"}:
        raise SynthesisPolicyDenied("external synthesis provider is not configured")
    if not policy.external_llm_enabled:
        raise SynthesisPolicyDenied("external LLM provider is disabled by provider policy")
    if not policy.ai_access_enabled:
        raise SynthesisPolicyDenied("AI archive access is disabled")
    if not policy.allowed_chat_ids:
        raise SynthesisPolicyDenied("external synthesis requires an explicit archive scope")
    if not policy.credentials_configured:
        raise SynthesisPolicyDenied("external synthesis credentials are not configured")
    if not policy.model:
        raise SynthesisPolicyDenied("external synthesis model is not configured")
    if policy.allowed_data_classes != _SERIALIZED_DATA_CLASSES:
        raise SynthesisPolicyDenied("external synthesis requires explicit permission for every serialized data class")
    if policy.max_evidence_items < 1 or policy.token_budget < 1:
        raise SynthesisPolicyDenied("external synthesis evidence budgets must be positive")


def bound_evidence(
    items: Iterable[Any],
    policy: SynthesisPolicy,
    *,
    question: str = "",
) -> tuple[ProviderEvidence, ...]:
    """Whitelist and deterministically bound evidence without accessing a DB.

    Input items can be ``SearchResult`` values or mappings containing only their
    public result fields.  Extra mapping keys (including paths and credentials)
    are intentionally ignored.
    """

    require_synthesis_policy(policy)
    _validate_question(question)
    safe: list[ProviderEvidence] = []
    seen_citations: set[str] = set()
    _require_request_within_budget(question, safe, policy)
    for item in items:
        chat_id, message_id, timestamp, chat_title, text = _read_evidence_item(item)
        if chat_id not in policy.allowed_chat_ids:
            continue
        citation = f"tg://chat/{chat_id}/message/{message_id}"
        if citation in seen_citations:
            continue
        evidence_id = f"e{len(safe) + 1}"
        candidate = ProviderEvidence(evidence_id, citation, timestamp, chat_title, text)
        if not _request_fits_budget(question, (*safe, candidate), policy):
            continue
        safe.append(candidate)
        seen_citations.add(citation)
        if len(safe) >= policy.max_evidence_items:
            break
    return tuple(safe)


def build_provider_request(
    question: str,
    items: Iterable[Any],
    policy: SynthesisPolicy,
) -> ProviderRequest:
    """Create a bounded request only after policy validation succeeds."""

    require_synthesis_policy(policy)
    evidence = bound_evidence(items, policy, question=question)
    return ProviderRequest(
        provider=policy.provider,
        model=policy.model or "",
        question=question,
        evidence=evidence,
        token_budget=policy.token_budget,
    )


def serialize_provider_request(request: ProviderRequest) -> str:
    """Encode the adapter input using only the declared request schema."""

    payload = {
        "question": request.question,
        "evidence": [_serialized_evidence(item) for item in request.evidence],
        "answer_contract": {"evidence_ids": [item.evidence_id for item in request.evidence]},
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _validate_question(question: str) -> None:
    if not isinstance(question, str):
        raise SynthesisPolicyDenied("external synthesis question must be a string")


def _request_fits_budget(question: str, evidence: Sequence[ProviderEvidence], policy: SynthesisPolicy) -> bool:
    request = ProviderRequest(
        provider=policy.provider,
        model=policy.model or "",
        question=question,
        evidence=tuple(evidence),
        token_budget=policy.token_budget,
    )
    return len(serialize_provider_request(request).encode("utf-8")) <= policy.token_budget


def _require_request_within_budget(question: str, evidence: Sequence[ProviderEvidence], policy: SynthesisPolicy) -> None:
    if not _request_fits_budget(question, evidence, policy):
        raise SynthesisPolicyDenied("external synthesis request exceeds the configured token budget")


def run_synthesis(
    question: str,
    items: Iterable[Any],
    policy: SynthesisPolicy,
    provider: SynthesisProvider | None = None,
    *,
    provider_factory: Callable[[], SynthesisProvider] | None = None,
    audit_sink: Callable[[SynthesisAudit], None] | None = None,
) -> SynthesisAnswer:
    """Run a single bounded answer attempt and fall back to cited local evidence.

    No exception from an adapter can trigger archive side effects because this
    function owns no archive handle and exposes no tool/action protocol.
    """

    if (provider is None) == (provider_factory is None):
        raise ValueError("provide exactly one synthesis provider or provider_factory")
    started = time.monotonic()
    try:
        request = build_provider_request(question, items, policy)
    except SynthesisPolicyDenied:
        _emit_audit(audit_sink, _audit(policy, (), False, started, "policy_denied"))
        raise
    if not request.evidence:
        answer = _extractive_answer(request.evidence, "no_evidence", policy)
        _emit_audit(audit_sink, _audit(policy, (), True, started, "no_evidence"))
        return answer
    try:
        selected_provider = provider if provider is not None else provider_factory()
        response = selected_provider.answer(request)
    except ProviderFailure as exc:
        status = "invalid_response" if exc.failure_class == "invalid_response" else "provider_unavailable"
        answer = _extractive_answer(request.evidence, status, policy, failure_class=exc.failure_class)
        _emit_audit(audit_sink, _audit(policy, request.evidence, True, started, status, exc.failure_class))
        return answer
    except Exception as exc:
        # Do not let adapter transport bugs or ordinary runtime failures bypass
        # the local fallback.  BaseException is intentionally not caught.
        failure_class = _exception_failure_class(exc)
        answer = _extractive_answer(request.evidence, "provider_unavailable", policy, failure_class=failure_class)
        _emit_audit(audit_sink, _audit(policy, request.evidence, True, started, "provider_unavailable", failure_class))
        return answer
    try:
        answer = validate_provider_response(response, request, policy)
    except (TypeError, ValueError):
        answer = _extractive_answer(request.evidence, "invalid_response", policy, failure_class="invalid_response")
        _emit_audit(audit_sink, _audit(policy, request.evidence, True, started, "invalid_response", "invalid_response"))
        return answer
    _emit_audit(audit_sink, _audit(policy, request.evidence, True, started, "synthesized", usage=response.usage))
    return answer


def validate_provider_response(
    response: ProviderResponse,
    request: ProviderRequest,
    policy: SynthesisPolicy,
) -> SynthesisAnswer:
    """Resolve only submitted evidence identifiers to their canonical citations."""

    if not isinstance(response.text, str) or not response.text.strip():
        raise ValueError("provider response text is empty")
    if not response.evidence_ids:
        raise ValueError("provider response is wholly uncited")
    permitted = {item.evidence_id: item.citation for item in request.evidence}
    if len(set(response.evidence_ids)) != len(response.evidence_ids):
        raise ValueError("provider response has duplicate evidence identifiers")
    unknown = [value for value in response.evidence_ids if value not in permitted]
    if unknown:
        raise ValueError("provider response references unknown evidence")
    citations = tuple(permitted[value] for value in response.evidence_ids)
    return SynthesisAnswer(
        answer=response.text.strip(),
        citations=citations,
        mode="synthesized",
        provider_status="synthesized",
        provider=policy.provider,
        model=policy.model,
        evidence_ids=response.evidence_ids,
        usage=response.usage,
    )


class DeterministicMockProvider:
    """A no-network contract adapter for deterministic tests and offline demos."""

    def __init__(self, *, text: str = "Synthesis based on supplied evidence.", evidence_ids: Sequence[str] | None = None) -> None:
        self.text = text
        self.evidence_ids = tuple(evidence_ids) if evidence_ids is not None else None
        self.calls: list[ProviderRequest] = []

    def answer(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        evidence_ids = self.evidence_ids if self.evidence_ids is not None else tuple(item.evidence_id for item in request.evidence[:1])
        return ProviderResponse(self.text, evidence_ids, ProviderUsage(input_tokens=estimate_tokens(serialize_provider_request(request)), output_tokens=estimate_tokens(self.text)))


def estimate_tokens(value: str | Mapping[str, Any]) -> int:
    """Stable conservative estimator used only for a local context cap."""

    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return max(1, (len(text) + 3) // 4)


def _read_evidence_item(item: Any) -> tuple[int, int, str, str, str]:
    if isinstance(item, Mapping):
        values = item
        chat_id = values.get("chat_id")
        message_id = values.get("message_id")
        timestamp = values.get("timestamp")
        chat_title = values.get("chat_title")
        text = values.get("text")
    else:
        chat_id = getattr(item, "chat_id", None)
        message_id = getattr(item, "message_id", None)
        timestamp = getattr(item, "timestamp", None)
        chat_title = getattr(item, "chat_title", None)
        text = getattr(item, "text", None)
    if not isinstance(chat_id, int) or not isinstance(message_id, int) or message_id < 0:
        raise ValueError("evidence requires integer chat_id and non-negative message_id")
    if not all(isinstance(value, str) for value in (timestamp, chat_title, text)):
        raise ValueError("evidence requires string timestamp, chat_title, and text")
    return chat_id, message_id, timestamp, chat_title, text


def _serialized_evidence(item: ProviderEvidence) -> dict[str, str]:
    return {
        "evidence_id": item.evidence_id,
        "citation": item.citation,
        "timestamp": item.timestamp,
        "chat_title": item.chat_title,
        "text": item.text,
    }


def local_cited_fallback(
    items: Iterable[Any],
    *,
    provider: str | None,
    model: str | None,
    provider_status: str,
    failure_class: str | None = None,
) -> SynthesisAnswer:
    """Return local cited evidence without constructing a provider request.

    This path is used for a denied external configuration after the caller has
    already selected one explicit local scope. It intentionally ignores every
    field except the public retrieval record fields and never calls a provider.
    """

    evidence: list[ProviderEvidence] = []
    seen: set[str] = set()
    for item in items:
        chat_id, message_id, timestamp, chat_title, text = _read_evidence_item(item)
        citation = f"tg://chat/{chat_id}/message/{message_id}"
        if citation in seen:
            continue
        seen.add(citation)
        evidence.append(ProviderEvidence(f"e{len(evidence) + 1}", citation, timestamp, chat_title, text))
    policy = SynthesisPolicy(provider=provider or "extractive", model=model)
    return _extractive_answer(tuple(evidence), provider_status, policy, failure_class=failure_class)


def _extractive_answer(
    evidence: Sequence[ProviderEvidence],
    status: str,
    policy: SynthesisPolicy,
    *,
    failure_class: str | None = None,
) -> SynthesisAnswer:
    if not evidence:
        text = "No matching archive evidence found."
    else:
        text = "Local extractive answer from retrieved evidence:\n" + "\n".join(
            f"- {item.citation}: {item.text}" for item in evidence
        )
    return SynthesisAnswer(
        answer=text,
        citations=tuple(item.citation for item in evidence),
        mode="extractive",
        provider_status=status,
        provider=policy.provider if policy.provider != "extractive" else None,
        model=policy.model,
        evidence_ids=tuple(item.evidence_id for item in evidence),
        failure_class=failure_class,
    )


def _audit(
    policy: SynthesisPolicy,
    evidence: Sequence[ProviderEvidence],
    allowed: bool,
    started: float,
    status: str,
    failure_class: str | None = None,
    usage: ProviderUsage | None = None,
) -> SynthesisAudit:
    return SynthesisAudit(
        provider=policy.provider,
        model=policy.model,
        policy_allowed=allowed,
        evidence_ids=tuple(item.evidence_id for item in evidence),
        evidence_count=len(evidence),
        token_budget=policy.token_budget,
        usage=usage,
        latency_ms=max(0, int((time.monotonic() - started) * 1000)),
        provider_status=status,
        failure_class=failure_class,
        effective_chat_ids=policy.allowed_chat_ids,
        effective_since=policy.effective_since,
        effective_until=policy.effective_until,
        effective_media_policy=policy.effective_media_policy,
    )


def _emit_audit(sink: Callable[[SynthesisAudit], None] | None, event: SynthesisAudit) -> None:
    if sink is not None:
        sink(event)


def _safe_failure_class(value: str) -> str:
    # Adapters may accidentally include request details in an exception.  Keep
    # only a small taxonomy, never a transformed version of arbitrary text.
    normalized = value.lower()
    for failure_class in ("timeout", "network", "authentication", "rate_limit", "unavailable", "configuration", "invalid_response"):
        if failure_class in normalized:
            return failure_class
    return "provider_failure"


def _exception_failure_class(exc: Exception) -> str:
    if isinstance(exc, OSError):
        return "network"
    if isinstance(exc, (TypeError, ValueError)):
        return "configuration"
    return _safe_failure_class(type(exc).__name__)
