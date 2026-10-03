from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Any

from .config import AppConfig, OPENROUTER_EMBEDDING_MODELS
from .context_budgeting import (
    ConservativeUtf8JsonTokenCounter,
    RetrievalBudgets,
    RetrievalStage,
    TokenAccounting,
    EvidenceItem,
    build_bounded_retrieval,
    canonical_json_bytes,
)
from .hybrid_retrieval import (
    EmbeddingProvider,
    RetrievalCandidate,
    RetrievalDecision,
    RetrievalMode,
    SemanticUnavailableError,
    SentenceTransformersLocalProvider,
    SourceType,
    decide_retrieval_mode,
    fuse_candidates,
)
from .models import SearchFilters, SearchResult
from .knowledge_catalog import (
    EvidenceMemberKind,
    EvidenceSetMember,
    EvidenceSetReference,
    ExpandedSource,
    KnowledgeCatalog,
    KnowledgeScope,
    RawCatalogReference,
    SourceReference,
    VersionMap,
    expand_sources,
)
from .llm_answers import (
    ProviderFailure,
    SynthesisAnswer,
    SynthesisAudit,
    SynthesisPolicyDenied,
    SynthesisProvider,
    local_cited_fallback,
    policy_from_config,
    require_synthesis_policy,
    run_synthesis,
)
from .openai_responses import OpenAIResponsesProvider
from .security import AgentOperation, AgentPolicyError, RequestedAgentScope, require_agent_policy
from .storage import Database


@dataclass(frozen=True)
class EvidenceContext:
    query: str
    items: list[SearchResult]

    def as_prompt_context(self) -> str:
        lines = [f"Query: {self.query}", "Evidence:"]
        for index, item in enumerate(self.items, start=1):
            lines.append(
                f"[{index}] {item.timestamp} {item.chat_title} "
                f"{item.citation}: {item.text}"
            )
        return "\n".join(lines)


class _NoCallProvider:
    """Policy-denial sentinel: reaching it would be a local programming error."""

    def answer(self, _request: object) -> object:
        raise AssertionError("provider must not be called after a policy denial")


class _UnavailableProvider:
    """Stable fallback for configured but unsupported provider identities."""

    def answer(self, _request: object) -> object:
        raise ProviderFailure("unavailable")


def _resolve_synthesis_scope(config: AppConfig, filters: SearchFilters, limit: int):
    """Use the central AI policy resolver for every external-synthesis caller."""

    requested_media = filters.media_type
    if filters.media_types is not None:
        requested_media = ",".join(filters.media_types)
    policy = config.ai_access
    return require_agent_policy(
        AgentOperation.ARCHIVE_READ,
        enabled=policy.enabled,
        allowed_chat_ids=policy.allowed_chat_ids,
        max_results=policy.max_results,
        allowed_since=policy.allowed_since,
        allowed_until=policy.allowed_until,
        allowed_media_types=policy.allowed_media_types,
        requested=RequestedAgentScope(
            chat_ids=(filters.chat_id,) if filters.chat_id is not None else (),
            since=filters.since,
            until=filters.until,
            media_policy=requested_media,
            result_limit=limit,
        ),
        automation=True,
    )


def _effective_synthesis_filters(requested: SearchFilters, decision: Any) -> SearchFilters:
    """Build the sole retrieval filter from the policy intersection."""

    media_policy = decision.media_policy
    media_types = None if media_policy in {None, "all"} else (() if media_policy == "none" else tuple(media_policy.split(",")))
    return SearchFilters(
        chat_id=decision.chat_ids[0],
        sender_id=requested.sender_id,
        since=decision.since,
        until=decision.until,
        media_types=media_types,
        has_link=requested.has_link,
    ).normalized()


@dataclass(frozen=True)
class EvidenceWindow:
    """One deduplicated Telegram citation plus bounded, filter-safe context."""

    citation: str
    score: float
    provenance: tuple[str, ...]
    source_types: tuple[str, ...]
    transcript_ids: tuple[int, ...]
    context: tuple[SearchResult, ...]
    context_truncated: bool = False

    def as_json(self) -> dict[str, Any]:
        return {
            "citation": self.citation,
            "context": [_search_result_json(item) for item in self.context],
            "context_truncated": self.context_truncated,
            "provenance": list(self.provenance),
            "score": self.score,
            "source_types": list(self.source_types),
            "transcript_ids": list(self.transcript_ids),
        }


@dataclass(frozen=True)
class HybridRetrievalResult:
    """Machine-readable retrieval provenance without provider/model paths."""

    mode: RetrievalMode
    fallback_reason: str | None
    index: dict[str, Any]
    candidate_limits: dict[str, int]
    evidence: tuple[EvidenceWindow, ...]
    token_budget: int
    truncated: bool
    accounting: TokenAccounting

    def as_json(self) -> dict[str, Any]:
        return {
            "candidate_limits": self.candidate_limits,
            "accounting": self.accounting.as_json(),
            "evidence": [item.as_json() for item in self.evidence],
            "fallback_reason": self.fallback_reason,
            "index": self.index,
            "mode": self.mode.value,
            "token_budget": self.token_budget,
            "truncated": self.truncated,
        }


class ArchiveAssistant:
    def __init__(self, db: Database, config: AppConfig | None = None, *, embedding_provider: EmbeddingProvider | None = None):
        self.db = db
        self.config = config
        self.embedding_provider = embedding_provider

    def retrieve(
        self,
        query: str,
        limit: int = 10,
        chat_id: int | None = None,
        filters: SearchFilters | None = None,
    ) -> EvidenceContext:
        effective_filters = filters or SearchFilters(chat_id=chat_id)
        return EvidenceContext(query=query, items=self.db.search(query=query, limit=limit, filters=effective_filters))

    def answer(
        self,
        query: str,
        limit: int = 10,
        chat_id: int | None = None,
        filters: SearchFilters | None = None,
    ) -> str:
        provider = self.config.llm.provider if self.config else "extractive"
        if provider != "extractive":
            if not self.config or not self.config.provider_policy.external_llm_enabled:
                raise PermissionError("external LLM provider is disabled by provider policy")
            raise NotImplementedError(f"LLM provider is configured but not implemented: {provider}")
        return self.extractive_answer(query, limit=limit, chat_id=chat_id, filters=filters)

    def extractive_answer(
        self,
        query: str,
        limit: int = 10,
        chat_id: int | None = None,
        filters: SearchFilters | None = None,
    ) -> str:
        """Local-only answer for read-only surfaces that never call a provider."""

        context = self.retrieve(query, limit=limit, chat_id=chat_id, filters=filters)
        if not context.items:
            return "No matching archive evidence found."
        lines = ["Local extractive answer from retrieved evidence:"]
        for item in context.items:
            lines.append(f"- {item.citation}: {item.text}")
        return "\n".join(lines)

    def answer_with_synthesis(
        self,
        query: str,
        *,
        limit: int,
        filters: SearchFilters,
        provider: SynthesisProvider | None = None,
        token_budget: int = 12_000,
    ) -> SynthesisAnswer:
        """Answer through one optional provider call over an exact local scope.

        The policy check happens before provider construction or prompt
        serialization. A configuration denial retains an explicitly scoped
        local extractive fallback and emits only sanitized audit metadata.
        """

        if self.config is None:
            return local_cited_fallback(
                (), provider=None, model=None, provider_status="policy_denied", failure_class="configuration"
            )
        normalized = filters.normalized()
        try:
            decision = _resolve_synthesis_scope(self.config, normalized, limit)
        except AgentPolicyError as exc:
            self._audit_policy_denial(exc, token_budget)
            return local_cited_fallback(
                (),
                provider=self.config.llm.provider,
                model=self.config.llm.model,
                provider_status="policy_denied",
                failure_class=exc.error_code,
            )
        effective_filters = _effective_synthesis_filters(normalized, decision)
        policy = policy_from_config(
            self.config,
            scope_chat_ids=decision.chat_ids,
            max_evidence_items=decision.result_limit or limit,
            token_budget=token_budget,
            effective_since=decision.since,
            effective_until=decision.until,
            effective_media_policy=decision.media_policy,
        )
        # The central decision is resolved before any archive access, prompt
        # construction, adapter import, or provider factory invocation.
        items = self.retrieve(query, limit=decision.result_limit or limit, filters=effective_filters).items
        audits: list[SynthesisAudit] = []
        try:
            require_synthesis_policy(policy)
        except SynthesisPolicyDenied:
            # Reuse the core's sanitized policy audit without constructing an
            # adapter or serializing the question/evidence.
            try:
                run_synthesis(query, items, policy, _NoCallProvider(), audit_sink=audits.append)
            except SynthesisPolicyDenied:
                pass
            self._audit_synthesis(audits, tuple(decision.chat_ids))
            return local_cited_fallback(
                items,
                provider=policy.provider,
                model=policy.model,
                provider_status="policy_denied",
                failure_class="policy_denied",
            )
        try:
            answer = run_synthesis(
                query,
                items,
                policy,
                provider,
                provider_factory=(lambda: self._configured_synthesis_provider(policy.provider)) if provider is None else None,
                audit_sink=audits.append,
            )
        except SynthesisPolicyDenied:
            self._audit_synthesis(audits, tuple(decision.chat_ids))
            return local_cited_fallback(
                items,
                provider=policy.provider,
                model=policy.model,
                provider_status="policy_denied",
                failure_class="budget_exceeded",
            )
        self._audit_synthesis(audits, tuple(decision.chat_ids))
        return answer

    def _configured_synthesis_provider(self, provider_name: str) -> SynthesisProvider:
        if provider_name == "openai-responses":
            return OpenAIResponsesProvider(api_key=self.config.llm.api_key or "")
        return _UnavailableProvider()

    def _audit_synthesis(self, audits: list[SynthesisAudit], scope_chat_ids: tuple[int, ...]) -> None:
        for audit in audits:
            self.db.audit("llm_answer", ",".join(str(value) for value in scope_chat_ids) or None, **audit.as_dict())

    def _audit_policy_denial(self, exc: AgentPolicyError, token_budget: int) -> None:
        self.db.audit(
            "llm_answer",
            None,
            provider=self.config.llm.provider,
            model=self.config.llm.model,
            policy_allowed=False,
            effective_chat_ids=[],
            effective_since=None,
            effective_until=None,
            effective_media_policy=None,
            evidence_ids=[],
            evidence_count=0,
            token_budget=token_budget,
            usage=None,
            latency_ms=0,
            provider_status="policy_denied",
            failure_class=exc.error_code,
        )

    def retrieve_hybrid(
        self,
        query: str,
        *,
        filters: SearchFilters,
        limit: int,
        token_budget: int,
        context_radius: int = 3,
        mode: RetrievalMode = RetrievalMode.AUTO,
        candidate_limit: int | None = None,
    ) -> HybridRetrievalResult:
        """Retrieve only scope-filtered candidates and compact cited windows.

        Both FTS and genuine-vector channels receive the exact same normalized
        filters before candidates are collected.  The optional provider is
        created only from an explicit, already-local configuration; no fallback
        invokes token overlap as if it were a vector query.
        """

        if limit < 1:
            raise ValueError("retrieval limit must be positive")
        if token_budget < 1:
            raise ValueError("token_budget must be positive")
        if context_radius < 0:
            raise ValueError("context radius must not be negative")
        effective_filters = filters.normalized()
        bounded_candidates = candidate_limit if candidate_limit is not None else min(max(limit * 3, limit), 100)
        if bounded_candidates < 1:
            raise ValueError("candidate_limit must be positive")

        provider, unavailable_reason = self._embedding_provider()
        index: dict[str, Any]
        if provider is None:
            index = self.db.embedding_index_status(filters=effective_filters)
            decision = _decide_without_provider(mode, unavailable_reason or "embedding_provider_unavailable")
        else:
            index = self.db.embedding_index_status(provider.metadata, effective_filters)
            decision = decide_retrieval_mode(
                mode,
                provider_available=True,
                index_current=index["status"] == "current",
            )

        keyword_candidates: list[RetrievalCandidate] = []
        semantic_candidates: list[RetrievalCandidate] = []
        if decision.mode in {RetrievalMode.KEYWORD, RetrievalMode.HYBRID}:
            keyword_candidates = _keyword_candidates(self.db.search(query, limit=bounded_candidates, filters=effective_filters))
        if decision.mode in {RetrievalMode.SEMANTIC, RetrievalMode.HYBRID}:
            if provider is None:  # Defensive: strict mode has already failed above.
                raise SemanticUnavailableError(unavailable_reason or "embedding_provider_unavailable")
            query_vectors = provider.embed([query])
            if len(query_vectors) != 1:
                raise SemanticUnavailableError("embedding_provider_invalid_query_vector")
            semantic_candidates = self.db.vector_candidates(
                tuple(query_vectors[0]), provider.metadata, filters=effective_filters, limit=bounded_candidates
            )

        if decision.mode == RetrievalMode.KEYWORD:
            ranked = fuse_candidates(keyword_candidates, (), limit=limit)
        elif decision.mode == RetrievalMode.SEMANTIC:
            ranked = fuse_candidates((), semantic_candidates, limit=limit)
        else:
            ranked = fuse_candidates(keyword_candidates, semantic_candidates, limit=limit)
        evidence, truncated, accounting = _bounded_windows(
            self.db,
            ranked,
            filters=effective_filters,
            context_radius=context_radius,
            token_budget=token_budget,
        )
        return HybridRetrievalResult(
            mode=decision.mode,
            fallback_reason=decision.fallback_reason,
            index=index,
            candidate_limits={"keyword": bounded_candidates, "semantic": bounded_candidates},
            evidence=evidence,
            token_budget=token_budget,
            truncated=truncated,
            accounting=accounting,
        )

    def _embedding_provider(self) -> tuple[EmbeddingProvider | None, str | None]:
        if self.embedding_provider is not None:
            return self.embedding_provider, None
        try:
            return local_embedding_provider(self.config), None
        except SemanticUnavailableError as exc:
            return None, exc.reason
        except ValueError:
            return None, "embedding_provider_unavailable"


def local_embedding_provider(config: AppConfig | None) -> EmbeddingProvider:
    """Construct only an explicitly configured provider from an existing path."""

    if config is None or not config.semantic.enabled:
        raise SemanticUnavailableError("embedding_provider_unavailable")
    from .hybrid_retrieval import LocalEmbeddingConfig, OpenRouterEmbeddingProvider

    if config.semantic.provider == "openrouter":
        if not config.provider_policy.external_embeddings_enabled:
            raise SemanticUnavailableError("external_embeddings_disabled")
        model = config.semantic.model
        if model not in OPENROUTER_EMBEDDING_MODELS:
            raise SemanticUnavailableError("openrouter_embedding_model_unavailable")
        import os
        return OpenRouterEmbeddingProvider(model, os.environ.get("OPENROUTER_API_KEY", ""), timeout_seconds=config.semantic.request_timeout_seconds)

    if config.semantic.provider != "sentence-transformers-local":
        raise SemanticUnavailableError("embedding_provider_unavailable")
    model_path = getattr(config.semantic, "model_path", None)
    if not model_path:
        raise SemanticUnavailableError("embedding_provider_unavailable")

    return SentenceTransformersLocalProvider(
        LocalEmbeddingConfig(
            Path(model_path),
            device=getattr(config.semantic, "device", "cpu"),
            batch_size=getattr(config.semantic, "batch_size", 32),
        )
    )


def _decide_without_provider(requested: RetrievalMode, reason: str) -> RetrievalDecision:
    if requested == RetrievalMode.KEYWORD:
        return RetrievalDecision(RetrievalMode.KEYWORD)
    if requested == RetrievalMode.AUTO:
        return RetrievalDecision(RetrievalMode.KEYWORD, fallback_reason=reason)
    raise SemanticUnavailableError(reason)


def _keyword_candidates(items: list[SearchResult]) -> list[RetrievalCandidate]:
    candidates = []
    for item in items:
        source_type = SourceType.TRANSCRIPT if item.transcript_id is not None else SourceType.MESSAGE
        source_id = item.transcript_id if item.transcript_id is not None else item.message_id
        # SQLite bm25 is lower-is-better (and frequently negative).  Search
        # already orders it, so an inverse stable rank is a portable score for
        # the documented per-channel normalization step.
        score = 1.0 / (len(candidates) + 1)
        candidates.append(
            RetrievalCandidate(
                citation=item.citation,
                chat_id=item.chat_id,
                message_id=item.message_id,
                text=item.text,
                source_type=source_type,
                source_id=source_id,
                score=score,
                channel=RetrievalMode.KEYWORD,
                transcript_id=item.transcript_id,
            )
        )
    return candidates


def _bounded_windows(
    db: Database,
    ranked: list[Any],
    *,
    filters: SearchFilters,
    context_radius: int,
    token_budget: int,
    ) -> tuple[tuple[EvidenceWindow, ...], bool, TokenAccounting]:
    counter = ConservativeUtf8JsonTokenCounter()
    budgets = RetrievalBudgets(item_limit=max(len(ranked), 1), context_radius=context_radius, token_budget=token_budget)
    usable_budget = budgets.usable_payload_budget
    seen_context: set[tuple[int, int]] = set()
    windows: list[EvidenceWindow] = []
    truncated = False
    for evidence in ranked:
        context = db.message_context(evidence.chat_id, evidence.message_id, radius=context_radius, filters=filters)
        selected: list[SearchResult] = []
        for item in context:
            key = (item.chat_id, item.message_id)
            if key in seen_context:
                continue
            selected.append(item)
        proposed = EvidenceWindow(
            citation=evidence.citation,
            score=evidence.score,
            provenance=evidence.provenance,
            source_types=tuple(item.value for item in evidence.source_types),
            transcript_ids=evidence.transcript_ids,
            context=tuple(selected),
        )
        fitted = _fit_evidence_window(proposed, windows, usable_budget, counter)
        if fitted is None:
            truncated = True
            break
        if fitted != proposed:
            truncated = True
        windows.append(fitted)
        seen_context.update((item.chat_id, item.message_id) for item in fitted.context)
    estimate = _count_evidence_payload(windows, counter)
    accounting = TokenAccounting(
        token_budget=token_budget,
        usable_payload_budget=usable_budget,
        estimated_tokens=estimate,
        counter=counter.name,
        counter_version=counter.version,
        safety_margin=budgets.safety_margin,
        truncated=truncated,
    )
    return tuple(windows), truncated, accounting


def _fit_evidence_window(
    proposed: EvidenceWindow,
    selected: list[EvidenceWindow],
    usable_budget: int,
    counter: ConservativeUtf8JsonTokenCounter,
) -> EvidenceWindow | None:
    """Admit a full window, then deterministically shrink context if needed."""

    for context_count in range(len(proposed.context), -1, -1):
        candidate = replace(proposed, context=proposed.context[:context_count], context_truncated=context_count < len(proposed.context))
        if _count_evidence_payload([*selected, candidate], counter) <= usable_budget:
            return candidate
    # A single very long first message can still retain its citation and the
    # largest UTF-8-safe prefix that fits the exact serialized JSON boundary.
    if proposed.context:
        original = proposed.context[0]
        low, high = 0, len(original.text)
        best: EvidenceWindow | None = None
        while low <= high:
            midpoint = (low + high) // 2
            shortened = replace(original, text=original.text[:midpoint])
            candidate = replace(proposed, context=(shortened,), context_truncated=True)
            if _count_evidence_payload([*selected, candidate], counter) <= usable_budget:
                best = candidate
                low = midpoint + 1
            else:
                high = midpoint - 1
        return best
    return None


def _count_evidence_payload(items: list[EvidenceWindow], counter: ConservativeUtf8JsonTokenCounter) -> int:
    return counter.count(canonical_json_bytes({"evidence": [item.as_json() for item in items]}))


def _search_result_json(item: SearchResult) -> dict[str, Any]:
    result = asdict(item)
    result["citation"] = item.citation
    return result


def knowledge_catalog_lookup(
    db: Database,
    *,
    profile_id: str,
    scope_id: str,
    chat_ids: tuple[int, ...],
    query: str,
    limit: int,
    filters: SearchFilters | None = None,
) -> dict[str, Any]:
    """Build a compact profile/saved-scope catalog without copying raw text.

    The runtime stores no fourth copy of Telegram messages.  Raw hits are
    therefore represented by citation and source version only; a caller must
    use :func:`expand_cited_sources` to fetch the selected original context.
    """

    scope = KnowledgeScope(profile_id, chat_ids)
    saved = db.get_scope(scope_id)
    if saved is None or tuple(sorted(saved["chat_ids"])) != scope.chat_ids:
        raise ValueError("knowledge scope is not the exact saved scope for this profile")
    if not isinstance(query, str) or not query.strip() or not 1 <= limit <= 50:
        raise ValueError("knowledge query requires a non-empty query and limit in 1..50")
    raw_rows: list[RawCatalogReference] = []
    versions: list[tuple[str, str]] = []
    # Search each explicitly authorized chat independently. SearchFilters has
    # a single chat boundary, so this cannot accidentally widen to the profile.
    for chat_id in scope.chat_ids:
        for result in db.search(query, limit=limit, filters=_filters_for_chat(filters, chat_id)):
            version = _source_version(result)
            source = SourceReference(result.citation, result.chat_id, "transcript" if result.transcript_id else "message", version)
            raw_rows.append(RawCatalogReference(source, tuple(query.split())))
            versions.append((source.citation, source.source_version))
    raw_by_citation = {item.source.citation: item for item in raw_rows}
    evidence_sets = []
    for view in db.list_evidence_sets(profile_id=profile_id, scope_id=scope_id, limit=50):
        if tuple(view["scope"]["chat_ids"]) != scope.chat_ids:
            continue
        if not _evidence_set_visible(db, view, filters):
            continue
        evidence_sets.append(_evidence_set_from_view(view, scope))
    catalog = KnowledgeCatalog(
        scope,
        raw=tuple(sorted(raw_by_citation.values(), key=lambda item: item.source.citation)),
        evidence_sets=tuple(sorted(evidence_sets, key=lambda item: item.evidence_set_id)),
        current_versions=VersionMap(raw=tuple(sorted({*versions})), wiki=()),
    )
    return {"catalog": catalog.as_json(), "hits": [item.as_json() for item in catalog.lookup(query, limit=limit)]}


def expand_cited_sources(
    db: Database,
    *,
    scope: KnowledgeScope,
    citations: tuple[str, ...],
    filters: SearchFilters | None = None,
    item_limit: int,
    context_radius: int,
    token_budget: int,
    stage_budget: int = 1,
    tool_call_budget: int = 1,
) -> dict[str, Any]:
    """Resolve explicit citations to bounded raw context through one read path."""

    budgets = RetrievalBudgets(
        item_limit=item_limit,
        context_radius=context_radius,
        token_budget=token_budget,
        stage_budget=stage_budget,
        tool_call_budget=tool_call_budget,
    )

    def resolver(received_scope: KnowledgeScope, requested: tuple[str, ...], limit: int) -> tuple[ExpandedSource, ...]:
        sources: list[ExpandedSource] = []
        for citation in requested:
            chat_id, message_id = _parse_citation(citation)
            context = db.message_context(chat_id, message_id, radius=budgets.context_radius, filters=_filters_for_chat(filters, chat_id))
            source = next((item for item in context if item.message_id == message_id), None)
            if source is None:
                continue
            text = "\n".join(f"{item.citation}: {item.text}" for item in context)
            sources.append(
                ExpandedSource(
                    SourceReference(citation, chat_id, "message", _source_version(source)),
                    text,
                )
            )
        return tuple(sources[:limit])

    expanded = expand_sources(scope, citations, resolver, limit=budgets.item_limit)
    candidates = [
        EvidenceItem(
            item.source.citation,
            item.text,
            source_type=item.source.source_type,
            metadata={
                "authority": "authoritative",
                "freshness": "current",
                "layer": "raw",
                "source_version": item.source.source_version,
            },
        )
        for item in expanded
    ]
    result = build_bounded_retrieval(
        candidates,
        budgets,
        stage=RetrievalStage.EXPANSION,
        tool_calls=1,
    )
    return {"operation": "source_expansion", **result.as_json()}


def _evidence_set_from_view(view: dict[str, Any], scope: KnowledgeScope) -> EvidenceSetReference:
    members = tuple(
        EvidenceSetMember(
            member["member_id"],
            EvidenceMemberKind(member["kind"]),
            member["logical_id"],
            tuple(source["citation"] for source in member["sources"]),
            member["version"],
        )
        for member in view["members"]
    )
    return EvidenceSetReference(
        view["evidence_set_id"],
        scope,
        view["purpose"],
        view["query"],
        members,
        view["summary"],
        view["created_at"],
        view["revision"],
        tuple(view["topics"]),
    )


def _evidence_set_visible(db: Database, view: dict[str, Any], filters: SearchFilters | None) -> bool:
    """Keep compact references inside the same date/media filter as raw hits."""

    for member in view["members"]:
        for source in member["sources"]:
            chat_id, message_id = _parse_citation(source["citation"])
            rows = db.message_context(
                chat_id,
                message_id,
                radius=0,
                filters=_filters_for_chat(filters, chat_id),
            )
            if not any(item.message_id == message_id for item in rows):
                return False
    return True


def _parse_citation(citation: str) -> tuple[int, int]:
    parts = citation.split("/")
    if len(parts) != 6 or parts[:3] != ["tg:", "", "chat"] or parts[4] != "message":
        raise ValueError("citation must be an exact tg://chat/.../message/... reference")
    try:
        return int(parts[3]), int(parts[5])
    except ValueError as exc:
        raise ValueError("citation must be an exact tg://chat/.../message/... reference") from exc


def _source_version(item: SearchResult) -> str:
    return sha256(f"{item.timestamp}\0{item.text}".encode("utf-8")).hexdigest()


def _filters_for_chat(filters: SearchFilters | None, chat_id: int) -> SearchFilters:
    base = (filters or SearchFilters()).normalized()
    return SearchFilters(
        chat_id=chat_id,
        sender_id=base.sender_id,
        since=base.since,
        until=base.until,
        media_type=base.media_type,
        media_types=base.media_types,
        has_link=base.has_link,
        chat_ids=base.chat_ids,
    ).normalized()
