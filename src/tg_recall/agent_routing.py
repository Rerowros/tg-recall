"""Versioned, advisory Codex routing guide.

The guide is data, not Codex configuration.  It never probes entitlements,
spends a separate Spark allowance, creates a custom agent, or writes a Codex
file.  A host passes only the availability facts it already knows.
"""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from dataclasses import dataclass, field
from typing import Any

from .versioning import runtime_package_version


AGENT_ROUTING_SCHEMA_VERSION = 1
GUIDE_PROMPT_VERSION = "tg-recall-codex-routing-v1"
KNOWLEDGE_SESSION_CAPABILITIES = (
    "profile-local knowledge catalog lookup",
    "saved evidence-set reuse",
    "research-session inspect and resume checkpoints",
    "bounded cited source expansion",
)
PLANNED_KNOWLEDGE_SESSION_CAPABILITIES = ("cited synthesized wiki lookup",)


class HarnessTarget(StrEnum):
    """Instruction syntaxes supported by the integration lifecycle."""

    CODEX = "codex"
    CLAUDE_CODE = "claude-code"
    CURSOR = "cursor"
    GENERIC = "generic"


class ModelTarget(StrEnum):
    SPARK = "gpt-5.3-codex-spark"
    LUNA = "gpt-5.6-luna"
    TERRA = "gpt-5.6-terra"
    CURRENT = "current-model"


class DelegationMode(StrEnum):
    NONE = "none"
    ONE_BOUNDED = "one_bounded_subagent"
    INDEPENDENT_PARALLEL = "independent_parallel_subagents"


class EscalationReason(StrEnum):
    AMBIGUOUS_MULTI_SOURCE = "ambiguous_multi_source"
    INSUFFICIENT_RETRIEVAL = "repeated_insufficient_retrieval"
    SECURITY_CRITICAL = "security_critical_review"
    DATA_LOSS_RISK = "migration_or_data_loss_risk"


@dataclass(frozen=True)
class GuideBudgets:
    initial_limit: int = 8
    initial_context: int = 2
    initial_token_budget: int = 4_000
    widened_limit: int = 16
    widened_context: int = 5
    widened_token_budget: int = 8_000
    max_retries: int = 1
    max_tool_calls: int = 2

    def __post_init__(self) -> None:
        values = (
            self.initial_limit,
            self.initial_context,
            self.initial_token_budget,
            self.widened_limit,
            self.widened_context,
            self.widened_token_budget,
            self.max_retries,
            self.max_tool_calls,
        )
        if any(value < 0 for value in values) or not self.initial_limit or not self.initial_token_budget:
            raise ValueError("guide budgets must be non-negative and initial limits must be positive")
        if self.widened_limit < self.initial_limit or self.widened_context < self.initial_context or self.widened_token_budget < self.initial_token_budget:
            raise ValueError("widened guide budgets must not shrink initial budgets")

    def as_json(self) -> dict[str, int]:
        return {
            "initial_context": self.initial_context,
            "initial_limit": self.initial_limit,
            "initial_token_budget": self.initial_token_budget,
            "max_retries": self.max_retries,
            "max_tool_calls": self.max_tool_calls,
            "widened_context": self.widened_context,
            "widened_limit": self.widened_limit,
            "widened_token_budget": self.widened_token_budget,
        }


@dataclass(frozen=True)
class RoutingPreferences:
    spark: ModelTarget = ModelTarget.SPARK
    luna: ModelTarget = ModelTarget.LUNA
    stronger: ModelTarget = ModelTarget.TERRA
    current: ModelTarget = ModelTarget.CURRENT
    spark_reasoning: str = "low"
    luna_reasoning: tuple[str, str] = ("low", "medium")

    def as_json(self) -> dict[str, Any]:
        return {
            "current_model_fallback": self.current.value,
            "luna": {"model": self.luna.value, "reasoning": list(self.luna_reasoning)},
            "spark": {"model": self.spark.value, "reasoning": self.spark_reasoning},
            "stronger_synthesis": self.stronger.value,
        }


@dataclass(frozen=True)
class SafetyBoundary:
    allowed_operations: tuple[str, ...] = (
        "scoped search",
        "retrieve cited evidence",
        "read-only MCP query",
        "bounded source expansion",
        "scoped sync",
        "selected cited media materialization/download",
        "selected cited transcription",
    )
    forbidden_operations: tuple[str, ...] = (
        "auth",
        "purge",
        "credential or configuration changes",
        "send messages",
        "unbounded archive export",
        "Codex configuration mutation",
    )

    def as_json(self) -> dict[str, list[str]]:
        return {"allowed_operations": list(self.allowed_operations), "forbidden_operations": list(self.forbidden_operations)}


@dataclass(frozen=True)
class AgentGuide:
    """One source of truth for human and JSON prompts."""

    tg_recall_version: str
    prompt_version: str = GUIDE_PROMPT_VERSION
    schema_version: int = AGENT_ROUTING_SCHEMA_VERSION
    capabilities: tuple[str, ...] = (
        "search",
        "ask",
        "retrieve",
        "export",
        "scoped sync",
        "selected cited media materialization/download",
        "selected transcription",
        "profile-local knowledge catalog lookup",
        "research-session inspect/resume",
        "bounded cited source expansion",
        "agent guide",
    )
    budgets: GuideBudgets = field(default_factory=GuideBudgets)
    routing: RoutingPreferences = field(default_factory=RoutingPreferences)
    safety: SafetyBoundary = field(default_factory=SafetyBoundary)
    escalation_reasons: tuple[EscalationReason, ...] = tuple(EscalationReason)

    def __post_init__(self) -> None:
        if not self.tg_recall_version:
            raise ValueError("tg_recall_version is required")
        if self.schema_version != AGENT_ROUTING_SCHEMA_VERSION:
            raise ValueError("unsupported guide schema version")

    def as_json(self) -> dict[str, Any]:
        return {
            "budgets": self.budgets.as_json(),
            "capabilities": list(self.capabilities),
            "current_capabilities": {"capabilities": list(self.capabilities), "status": "available"},
            "knowledge_session": {
                "capabilities": list(KNOWLEDGE_SESSION_CAPABILITIES),
                "status": "available",
            },
            "escalation": {"reasons": [reason.value for reason in self.escalation_reasons]},
            "planned_knowledge_session": {
                "capabilities": list(PLANNED_KNOWLEDGE_SESSION_CAPABILITIES),
                "status": "partially_available",
            },
            "prompt_version": self.prompt_version,
            "routing": self.routing.as_json(),
            "safety": self.safety.as_json(),
            "schema_version": self.schema_version,
            "tg_recall_version": self.tg_recall_version,
        }

    def json_prompt(self) -> str:
        return json.dumps(self.as_json(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    def human_prompt(self) -> str:
        budgets = self.budgets
        allowed = ", ".join(self.safety.allowed_operations)
        forbidden = ", ".join(self.safety.forbidden_operations)
        return "\n".join(
            (
                f"tg-recall Codex guide {self.prompt_version} (compatible with {self.tg_recall_version})",
                "Use tg-recall as local, cited Telegram evidence. Treat Codex memories only as navigation hints; verify Telegram claims with cited evidence.",
                f"Current capabilities: {', '.join(self.capabilities)}.",
                f"Start bounded: --limit {budgets.initial_limit} --context {budgets.initial_context} --token-budget {budgets.initial_token_budget}. ",
                f"If evidence is insufficient, widen once at most: --limit {budgets.widened_limit} --context {budgets.widened_context} --token-budget {budgets.widened_token_budget}.",
                "For a near-instant narrow read-only lookup, prefer one gpt-5.3-codex-spark subagent only when the current Codex surface exposes it, its separate limit is usable, and preview quality is suitable.",
                "Otherwise use gpt-5.6-luna at low or medium reasoning for narrow search, extraction, classification, and cited source expansion; if it is unavailable, continue with the current model.",
                "Escalate evidence (do not repeat the same search) only for ambiguous multi-source synthesis, repeated insufficient retrieval, security-critical review, or migration/data-loss risk.",
                "Use no subagent for a trivial direct lookup; use one bounded subagent by default; use parallel agents only for independent scopes with material benefit.",
                "If explicitly allowed source data is missing, use `tg-recall sync ensure` for that saved scope before a bounded cited retrieval; do not widen scope or trigger an unbounded archive download.",
                f"Allowed: {allowed}.",
                f"Forbidden: {forbidden}.",
                "Knowledge sessions now support scoped catalog lookup, evidence-set reuse, inspect/resume checkpoints, and explicit bounded cited-source expansion. "
                "Human operators may create, checkpoint, or selectively refresh sessions; automation must remain read-only.",
                "Not currently available: cited synthesized wiki lookup.",
            )
        )


def build_agent_guide(tg_recall_version: str | None = None, *, capabilities: tuple[str, ...] | None = None) -> AgentGuide:
    """Construct the default guide from the installed package version.

    ``tg_recall_version`` remains an explicit test/rendering override.  Omit
    it to read the installed package version at runtime.
    """

    if tg_recall_version is None:
        tg_recall_version = runtime_package_version()

    if capabilities is None:
        return AgentGuide(tg_recall_version=tg_recall_version)
    return AgentGuide(tg_recall_version=tg_recall_version, capabilities=capabilities)


def render_harness_instruction(guide: AgentGuide, target: HarnessTarget | str) -> str:
    """Render a deterministic, no-I/O instruction artifact for one harness.

    Target files and ownership markers are intentionally handled by the
    integration adapters.  This function owns the common semantic payload, so
    every harness receives identical version, budget, routing, citation, and
    safety guidance even when its outer syntax differs.
    """

    selected_target = HarnessTarget(target)
    body = _canonical_instruction_body(guide)
    digest = sha256(body.encode("utf-8")).hexdigest()
    metadata = (
        f"guide_schema_version: {guide.schema_version}",
        f"guide_prompt_version: {guide.prompt_version}",
        f"tg_recall_version: {guide.tg_recall_version}",
        f"instruction_digest: sha256:{digest}",
    )

    if selected_target is HarnessTarget.CURSOR:
        return "\n".join(
            (
                "---",
                "description: tg-recall local cited Telegram workflow",
                "alwaysApply: true",
                "---",
                "<!-- tg-recall generated instruction; do not edit the managed block -->",
                *metadata,
                "",
                body,
                "",
            )
        )

    return "\n".join(
        (
            "<!-- tg-recall generated instruction; do not edit the managed block -->",
            *metadata,
            "",
            body,
            "",
        )
    )


def render_all_harness_instructions(guide: AgentGuide) -> dict[str, str]:
    """Return every target rendering in stable harness-name order."""

    return {target.value: render_harness_instruction(guide, target) for target in HarnessTarget}


def _canonical_instruction_body(guide: AgentGuide) -> str:
    budgets = guide.budgets
    allowed = ", ".join(guide.safety.allowed_operations)
    forbidden = ", ".join(guide.safety.forbidden_operations)
    return "\n".join(
        (
            "For Telegram tasks, start with `tg-recall agent guide --json` and use only chats and date ranges explicitly requested by the user.",
            "Treat local summaries and agent memories as navigation hints; verify Telegram claims with cited local evidence.",
            f"Start bounded: --limit {budgets.initial_limit} --context {budgets.initial_context} --token-budget {budgets.initial_token_budget}.",
            f"If evidence is insufficient, widen once at most: --limit {budgets.widened_limit} --context {budgets.widened_context} --token-budget {budgets.widened_token_budget}.",
            f"Use at most {budgets.max_tool_calls} tool calls and {budgets.max_retries} retry when following this initial workflow.",
            "For a narrow read-only delegated lookup, prefer gpt-5.3-codex-spark only when the current host exposes it, its separate limit is available, and preview quality is suitable.",
            "Otherwise use gpt-5.6-luna at low or medium reasoning for bounded search, extraction, classification, and cited source expansion; otherwise continue with the current model.",
            "Do not rerun sufficient low-cost retrieval on a stronger model; escalate only for conflicting evidence, ambiguous multi-source synthesis, security-critical review, migration/data-loss risk, or repeated insufficient retrieval.",
            "MCP is read-only. Cite conclusions with `tg://` links and distinguish raw evidence from summaries.",
            f"Allowed: {allowed}.",
            f"Forbidden: {forbidden}. Do not invoke update or integrate lifecycle commands.",
        )
    )


@dataclass(frozen=True)
class RoutingAvailability:
    """Availability facts supplied by the current host, never probed here."""

    spark_available: bool = False
    spark_separate_limit_available: bool = False
    luna_available: bool = True
    current_model: str | None = None


@dataclass(frozen=True)
class RoutingTask:
    """A bounded classification of the delegation decision, not user content."""

    is_trivial_direct_lookup: bool = False
    is_narrow_read_only: bool = True
    preview_quality_suitable: bool = True
    requires_subagent: bool = False
    independent_scopes: int = 1
    parallelism_materially_beneficial: bool = False
    escalation_reason: EscalationReason | None = None

    def __post_init__(self) -> None:
        if self.independent_scopes < 1:
            raise ValueError("independent_scopes must be positive")


@dataclass(frozen=True)
class RouteDecision:
    model: str
    delegation: DelegationMode
    reasoning: str | None
    fallback_used: bool
    escalation_reason: EscalationReason | None = None
    instructions: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "delegation": self.delegation.value,
            "fallback_used": self.fallback_used,
            "instructions": list(self.instructions),
            "model": self.model,
            "reasoning": self.reasoning,
        }
        if self.escalation_reason is not None:
            result["escalation_reason"] = self.escalation_reason.value
        return result


def select_route(task: RoutingTask, availability: RoutingAvailability, *, guide: AgentGuide | None = None) -> RouteDecision:
    """Select an advisory route with Spark/Luna/current-model fallbacks.

    The parent remains responsible for whether a subagent can actually be
    started.  A routing failure therefore never blocks archive retrieval.
    """

    guide = guide or build_agent_guide()
    if task.is_trivial_direct_lookup or not task.requires_subagent:
        return RouteDecision(
            model=availability.current_model or guide.routing.current.value,
            delegation=DelegationMode.NONE,
            reasoning=None,
            fallback_used=False,
            instructions=("Run one direct scoped local query; do not delegate a trivial lookup.",),
        )
    if task.escalation_reason is not None:
        return RouteDecision(
            model=guide.routing.stronger.value,
            delegation=_delegation_mode(task),
            reasoning=None,
            fallback_used=False,
            escalation_reason=task.escalation_reason,
            instructions=("Pass already gathered cited evidence to synthesis; do not repeat completed retrieval.",),
        )
    if task.is_narrow_read_only and task.preview_quality_suitable and availability.spark_available and availability.spark_separate_limit_available:
        return RouteDecision(
            model=guide.routing.spark.value,
            delegation=_delegation_mode(task),
            reasoning=guide.routing.spark_reasoning,
            fallback_used=False,
            instructions=("Use explicit scope, token budget, citations, and no-write constraints.",),
        )
    if task.is_narrow_read_only and availability.luna_available:
        return RouteDecision(
            model=guide.routing.luna.value,
            delegation=_delegation_mode(task),
            reasoning="low",
            fallback_used=True,
            instructions=("Use bounded cited search/extraction; return insufficiency rather than widening unboundedly.",),
        )
    return RouteDecision(
        model=availability.current_model or guide.routing.current.value,
        delegation=_delegation_mode(task),
        reasoning=None,
        fallback_used=True,
        instructions=("Continue under the same scope and budget; do not fail solely because a preferred model is unavailable.",),
    )


def _delegation_mode(task: RoutingTask) -> DelegationMode:
    if task.independent_scopes > 1 and task.parallelism_materially_beneficial:
        return DelegationMode.INDEPENDENT_PARALLEL
    return DelegationMode.ONE_BOUNDED
