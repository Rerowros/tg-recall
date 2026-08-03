from __future__ import annotations

import tg_recall.versioning as versioning
from tg_recall import __version__

from tg_recall.agent_routing import (
    AGENT_ROUTING_SCHEMA_VERSION,
    GUIDE_PROMPT_VERSION,
    DelegationMode,
    EscalationReason,
    HarnessTarget,
    ModelTarget,
    RoutingAvailability,
    RoutingTask,
    build_agent_guide,
    render_all_harness_instructions,
    render_harness_instruction,
    select_route,
)
from tg_recall.versioning import runtime_package_version


def test_one_schema_generates_stable_json_and_human_prompts() -> None:
    guide = build_agent_guide()
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
    assert payload["current_capabilities"]["status"] == "available"
    assert "current_v0_2" not in payload
    assert payload["knowledge_session"]["status"] == "available"
    assert "research-session inspect and resume checkpoints" in payload["knowledge_session"]["capabilities"]
    assert payload["planned_knowledge_session"]["status"] == "partially_available"
    assert "Current capabilities" in guide.human_prompt()
    assert "v0.2" not in guide.human_prompt()
    assert "auth" in payload["safety"]["forbidden_operations"]
    assert "purge" in payload["safety"]["forbidden_operations"]
    assert guide.json_prompt().startswith('{"budgets"')


def test_runtime_version_is_used_only_for_default_guide_argument(monkeypatch) -> None:
    monkeypatch.setattr("tg_recall.agent_routing.runtime_package_version", lambda: "9.8.7")

    assert build_agent_guide().tg_recall_version == "9.8.7"
    assert build_agent_guide("0.2.0").tg_recall_version == "0.2.0"
    assert build_agent_guide("4.3.2").tg_recall_version == "4.3.2"
    assert runtime_package_version()


def test_runtime_package_version_uses_metadata_then_source_fallback(monkeypatch) -> None:
    monkeypatch.setattr(versioning, "distribution_version", lambda _: "7.6.5")
    assert versioning.runtime_package_version() == "7.6.5"

    def missing_metadata(_: str) -> str:
        raise versioning.PackageNotFoundError

    monkeypatch.setattr(versioning, "distribution_version", missing_metadata)
    assert versioning.runtime_package_version() == __version__


def test_harness_instruction_renderers_match_golden_output_and_semantics() -> None:
    guide = build_agent_guide("0.5.0")
    rendered = render_all_harness_instructions(guide)

    assert list(rendered) == [target.value for target in HarnessTarget]
    expected_markdown = """<!-- tg-recall generated instruction; do not edit the managed block -->
guide_schema_version: 1
guide_prompt_version: tg-recall-codex-routing-v1
tg_recall_version: 0.5.0
instruction_digest: sha256:9039e01781dde4694c1b095c4fd14f12348d40a5654da1a93b7d46502d2233eb

For Telegram tasks, start with `tg-recall agent guide --json` and use only chats and date ranges explicitly requested by the user.
Treat local summaries and agent memories as navigation hints; verify Telegram claims with cited local evidence.
Start bounded: --limit 8 --context 2 --token-budget 4000.
If evidence is insufficient, widen once at most: --limit 16 --context 5 --token-budget 8000.
Use at most 2 tool calls and 1 retry when following this initial workflow.
For a narrow read-only delegated lookup, prefer gpt-5.3-codex-spark only when the current host exposes it, its separate limit is available, and preview quality is suitable.
Otherwise use gpt-5.6-luna at low or medium reasoning for bounded search, extraction, classification, and cited source expansion; otherwise continue with the current model.
Do not rerun sufficient low-cost retrieval on a stronger model; escalate only for conflicting evidence, ambiguous multi-source synthesis, security-critical review, migration/data-loss risk, or repeated insufficient retrieval.
MCP is read-only. Cite conclusions with `tg://` links and distinguish raw evidence from summaries.
Allowed: scoped search, retrieve cited evidence, read-only MCP query, bounded source expansion, scoped sync, selected cited media materialization/download, selected cited transcription.
Forbidden: auth, purge, credential or configuration changes, send messages, unbounded archive export, Codex configuration mutation. Do not invoke update or integrate lifecycle commands.
"""
    assert rendered[HarnessTarget.CODEX.value] == expected_markdown
    assert rendered[HarnessTarget.CODEX.value] == render_harness_instruction(guide, HarnessTarget.CODEX)
    assert rendered[HarnessTarget.CLAUDE_CODE.value] == expected_markdown
    assert rendered[HarnessTarget.GENERIC.value] == expected_markdown
    assert rendered[HarnessTarget.CURSOR.value] == (
        "---\ndescription: tg-recall local cited Telegram workflow\nalwaysApply: true\n---\n" + expected_markdown
    )

    required = (
        "guide_schema_version: 1",
        f"guide_prompt_version: {GUIDE_PROMPT_VERSION}",
        "tg_recall_version: 0.5.0",
        "instruction_digest: sha256:",
        "--limit 8 --context 2 --token-budget 4000",
        "--limit 16 --context 5 --token-budget 8000",
        "gpt-5.3-codex-spark",
        "gpt-5.6-luna",
        "Cite conclusions with `tg://` links",
        "Forbidden: auth, purge, credential or configuration changes, send messages, unbounded archive export, Codex configuration mutation.",
        "Do not invoke update or integrate lifecycle commands.",
    )
    for artifact in rendered.values():
        for expected in required:
            assert expected in artifact

    expected_body = render_harness_instruction(guide, HarnessTarget.CODEX).split("\n\n", maxsplit=1)[1]
    cursor_body = render_harness_instruction(guide, HarnessTarget.CURSOR).split("\n\n", maxsplit=1)[1]
    assert cursor_body == expected_body


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
