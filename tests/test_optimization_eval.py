from __future__ import annotations

import json
from pathlib import Path

import pytest

from tg_recall.optimization_eval import (
    AuthoritativeCost,
    EVALUATION_SCHEMA_VERSION,
    EvaluationMetrics,
    ProviderUsage,
    RepresentativeTask,
    RepresentativeTaskKind,
    StaleHandling,
    compare_configurations,
    evaluate_task,
    load_representative_tasks,
)


FIXTURES = Path(__file__).parent / "fixtures" / "optimization_eval_tasks.json"


def metrics(task: RepresentativeTask, configuration: str, **changes: object) -> EvaluationMetrics:
    values: dict[str, object] = {
        "configuration": configuration,
        "quality": 1.0,
        "citations": task.expected_citations,
        "citation_coverage": 1.0,
        "stale_handling": task.expected_stale_handling,
        "turns": 2,
        "retrieval_calls": 2,
        "retrieval_stages": ("narrow_raw", "context"),
        "returned_items": len(task.expected_citations),
        "returned_context": (f"Synthetic context: {' '.join(task.required_evidence_terms)}. {' '.join(task.expected_citations)}",),
        "deduplicated_items": len(task.expected_citations),
        "estimated_tokens": 800,
        "latency_ms": 100.0,
    }
    values.update(changes)
    return EvaluationMetrics(**values)  # type: ignore[arg-type]


def test_sanitized_fixture_has_every_representative_task_kind() -> None:
    tasks = load_representative_tasks(FIXTURES)
    assert {task.kind for task in tasks} == set(RepresentativeTaskKind)
    assert all("synthetic" in task.task_id for task in tasks)
    assert all(citation.startswith("tg://chat/") for task in tasks for citation in task.expected_citations)


def test_lower_token_or_turn_cost_cannot_hide_missing_citation_or_quality() -> None:
    task = load_representative_tasks(FIXTURES)[0]
    comparison = evaluate_task(
        task,
        metrics(task, "baseline"),
        metrics(task, "optimized", citations=(), citation_coverage=0.0, quality=0.9, estimated_tokens=1, turns=0, retrieval_calls=0),
    )
    assert not comparison.passed
    assert comparison.estimated_token_delta == -799
    assert "optimized_quality_below_fixture_threshold" in comparison.failures
    assert "optimized_citation_coverage_incomplete" in comparison.failures


def test_stale_handling_is_an_evidence_gate() -> None:
    task = next(task for task in load_representative_tasks(FIXTURES) if task.kind == RepresentativeTaskKind.CONFLICTING_EVIDENCE)
    comparison = evaluate_task(task, metrics(task, "baseline"), metrics(task, "optimized", stale_handling=StaleHandling.CURRENT_CHECKED))
    assert not comparison.passed
    assert "optimized_stale_handling_incorrect" in comparison.failures


def test_reported_citation_coverage_cannot_disagree_with_citations() -> None:
    task = load_representative_tasks(FIXTURES)[0]
    comparison = evaluate_task(task, metrics(task, "baseline"), metrics(task, "optimized", citations=(), citation_coverage=1.0))
    assert not comparison.passed
    assert "reported_citation_coverage_does_not_match_citations" in comparison.failures


def test_claimed_quality_and_citations_do_not_replace_required_evidence_content() -> None:
    task = load_representative_tasks(FIXTURES)[0]
    comparison = evaluate_task(
        task,
        metrics(task, "baseline"),
        metrics(
            task,
            "optimized",
            quality=1.0,
            citations=task.expected_citations,
            citation_coverage=1.0,
            returned_context=("Synthetic unrelated status note; no required task evidence.",),
            estimated_tokens=1,
            turns=0,
            retrieval_calls=0,
        ),
    )
    assert not comparison.passed
    assert comparison.estimated_token_delta == -799
    assert "optimized_required_evidence_missing:Aster,deadline" in comparison.failures


def test_cost_requires_authoritative_actual_usage() -> None:
    task = load_representative_tasks(FIXTURES)[0]
    with pytest.raises(ValueError, match="authoritative provider usage"):
        metrics(task, "optimized", cost=AuthoritativeCost(0.01, "USD", "provider-invoice"))
    report = compare_configurations(
        [task],
        lambda item: metrics(item, "baseline"),
        lambda item: metrics(
            item,
            "optimized",
            provider_usage=ProviderUsage("synthetic-provider", "synthetic-model", "provider-usage", input_tokens=10, authoritative=True),
            cost=AuthoritativeCost(0.01, "USD", "provider-invoice"),
        ),
    )
    assert report.authoritative_cost_per_successful_task == 0.01


def test_comparison_runner_executes_both_configurations_for_every_task() -> None:
    tasks = load_representative_tasks(FIXTURES)
    baseline_calls: list[str] = []
    optimized_calls: list[str] = []

    def baseline(task: RepresentativeTask) -> EvaluationMetrics:
        baseline_calls.append(task.task_id)
        return metrics(task, "baseline")

    def optimized(task: RepresentativeTask) -> EvaluationMetrics:
        optimized_calls.append(task.task_id)
        return metrics(task, "optimized", estimated_tokens=400, turns=1, retrieval_calls=1, latency_ms=50.0)

    report = compare_configurations(tasks, baseline, optimized)
    assert report.schema_version == EVALUATION_SCHEMA_VERSION
    assert report.passed
    assert set(baseline_calls) == {task.task_id for task in tasks}
    assert set(optimized_calls) == {task.task_id for task in tasks}
    serialized = report.to_dict()
    assert json.loads(json.dumps(serialized))["schema_version"] == EVALUATION_SCHEMA_VERSION
    assert serialized["task_count"] == 5
    assert serialized["authoritative_cost_per_successful_task"] is None
    assert all(item["estimated_token_delta"] == -400 for item in serialized["comparisons"])
    assert all(item["turn_delta"] == -1 for item in serialized["comparisons"])
