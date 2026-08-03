from __future__ import annotations

from tg_recall.agent_routing import (
    AGENT_ROUTING_SCHEMA_VERSION,
    GUIDE_PROMPT_VERSION,
    DelegationMode,
    EscalationReason,
    ModelTarget,
    RoutingAvailability,
    RoutingTask,
    build_agent_guide,
    select_route,
)


def test_one_schema_generates_stable_json_and_human_prompts() -> None:
    guide = build_agent_guide("0.2.0")
    payload = guide.as_json()

    assert payload["schema_version"] == AGENT_ROUTING_SCHEMA_VERSION
    assert payload["prompt_version"] == GUIDE_PROMPT_VERSION
    assert payload["routing"]["spark"]["model"] == ModelTarget.SPARK.value
    assert "gpt-5.3-codex-spark" in guide.human_prompt()
    assert "gpt-5.6-luna" in guide.human_prompt()
    assert "Codex configuration mutation" in guide.human_prompt()
    assert "scoped sync" in payload["safety"]["allowed_operations"]
    assert "selected cited media materialization/download" in guide.human_prompt()
    assert "selected cited transcription" in payload["safety"]["allowed_operations"]
    assert payload["current_v0_2"]["status"] == "available"
    assert payload["knowledge_session"]["status"] == "available"
    assert "research-session inspect and resume checkpoints" in payload["knowledge_session"]["capabilities"]
    assert payload["planned_knowledge_session"]["status"] == "partially_available"
    assert "Current v0.2-compatible capabilities" in guide.human_prompt()
    assert "Planned, not available in v0.2 CLI or MCP" in guide.human_prompt()
    assert "auth" in payload["safety"]["forbidden_operations"]
    assert "purge" in payload["safety"]["forbidden_operations"]
    assert guide.json_prompt().startswith('{"budgets"')


def test_spark_is_recommended_only_for_available_separate_limit_and_suitable_narrow_work() -> None:
    task = RoutingTask(requires_subagent=True, is_narrow_read_only=True, preview_quality_suitable=True)
    route = select_route(task, RoutingAvailability(spark_available=True, spark_separate_limit_available=True))

    assert route.model == ModelTarget.SPARK.value
    assert route.delegation == DelegationMode.ONE_BOUNDED
    assert route.reasoning == "low"
    assert route.fallback_used is False


def test_luna_low_cost_fallback_handles_missing_or_exhausted_spark() -> None:
    task = RoutingTask(requires_subagent=True)
    route = select_route(task, RoutingAvailability(spark_available=True, spark_separate_limit_available=False, luna_available=True))

    assert route.model == ModelTarget.LUNA.value
    assert route.reasoning == "low"
    assert route.fallback_used is True


def test_current_model_fallback_never_fails_lookup_when_subagent_model_is_unavailable() -> None:
    route = select_route(
        RoutingTask(requires_subagent=True),
        RoutingAvailability(spark_available=False, luna_available=False, current_model="configured-parent"),
    )

    assert route.model == "configured-parent"
    assert route.delegation == DelegationMode.ONE_BOUNDED
    assert route.fallback_used is True


def test_trivial_lookup_does_not_spend_a_subagent_turn() -> None:
    route = select_route(
        RoutingTask(is_trivial_direct_lookup=True, requires_subagent=True),
        RoutingAvailability(spark_available=True, spark_separate_limit_available=True, current_model="parent"),
    )

    assert route.model == "parent"
    assert route.delegation == DelegationMode.NONE


def test_escalation_preserves_evidence_and_only_uses_stronger_route_for_stated_boundary() -> None:
    route = select_route(
        RoutingTask(requires_subagent=True, escalation_reason=EscalationReason.AMBIGUOUS_MULTI_SOURCE),
        RoutingAvailability(spark_available=True, spark_separate_limit_available=True),
    )

    assert route.model == ModelTarget.TERRA.value
    assert route.escalation_reason == EscalationReason.AMBIGUOUS_MULTI_SOURCE
    assert "do not repeat completed retrieval" in route.instructions[0]


def test_parallel_delegation_is_limited_to_independent_material_scopes() -> None:
    parallel = select_route(
        RoutingTask(requires_subagent=True, independent_scopes=2, parallelism_materially_beneficial=True),
        RoutingAvailability(spark_available=False, luna_available=True),
    )
    sequential = select_route(
        RoutingTask(requires_subagent=True, independent_scopes=2, parallelism_materially_beneficial=False),
        RoutingAvailability(spark_available=False, luna_available=True),
    )

    assert parallel.delegation == DelegationMode.INDEPENDENT_PARALLEL
    assert sequential.delegation == DelegationMode.ONE_BOUNDED
