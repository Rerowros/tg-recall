"""Deterministic, offline evaluation gate for agent-context optimizations.

The fixtures describe synthetic evidence only.  Runners are injected callables
so this foundation never contacts Telegram, an LLM, or a billing provider.
``estimated_tokens`` is retrieval-payload telemetry, never billed usage.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
import json
import math
from pathlib import Path
from typing import Any


EVALUATION_SCHEMA_VERSION = "1"


class RepresentativeTaskKind(StrEnum):
    FRESH_LOOKUP = "fresh_lookup"
    REPEATED_LOOKUP = "repeated_lookup"
    OLD_MESSAGE_EXPANSION = "old_message_expansion"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    RESUMED_RESEARCH = "resumed_research"


class StaleHandling(StrEnum):
    CURRENT_CHECKED = "current_checked"
    SOURCE_EXPANDED = "source_expanded"
    CONFLICT_FLAGGED = "conflict_flagged"
    RESUMED_REVALIDATED = "resumed_revalidated"


@dataclass(frozen=True)
class RepresentativeTask:
    """A completely synthetic research task and its minimum evidence contract."""

    task_id: str
    kind: RepresentativeTaskKind
    query: str
    scope_chat_ids: tuple[int, ...]
    expected_citations: tuple[str, ...]
    required_evidence_terms: tuple[str, ...]
    expected_stale_handling: StaleHandling
    minimum_quality: float = 1.0

    def __post_init__(self) -> None:
        if not self.task_id or not self.scope_chat_ids or not self.expected_citations:
            raise ValueError("representative task requires id, scope, and expected citations")
        if not 0 <= self.minimum_quality <= 1:
            raise ValueError("minimum_quality must be between zero and one")
        if len(set(self.expected_citations)) != len(self.expected_citations):
            raise ValueError("expected citations must be unique")


@dataclass(frozen=True)
class ProviderUsage:
    """Actual usage supplied by a host/provider, separate from local estimates."""

    provider: str
    model: str
    provenance: str
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    output_tokens: int | None = None
    authoritative: bool = False

    def __post_init__(self) -> None:
        if not self.provider or not self.model or not self.provenance:
            raise ValueError("provider usage requires provider, model, and provenance")
        for value in (self.input_tokens, self.cached_input_tokens, self.reasoning_tokens, self.output_tokens):
            if value is not None and value < 0:
                raise ValueError("provider usage token counts cannot be negative")


@dataclass(frozen=True)
class AuthoritativeCost:
    """A cost is valid only when a provider explicitly supplied it."""

    amount: float
    currency: str
    provenance: str
    authoritative: bool = True

    def __post_init__(self) -> None:
        if not math.isfinite(self.amount) or self.amount < 0:
            raise ValueError("cost amount must be a finite non-negative value")
        if not self.currency or not self.provenance or not self.authoritative:
            raise ValueError("cost requires authoritative provider provenance")


@dataclass(frozen=True)
class EvaluationMetrics:
    """Versioned baseline/optimized result schema for a single task run.

    ``returned_context`` contains rendered context windows, not an item-per-row
    ledger: one window may include several returned or deduplicated items.  The
    evaluation gate casefolds the joined windows and requires every fixture
    term to occur as a normalized substring.  This is deliberately a
    deterministic evidence-presence check, not semantic answer scoring.
    """

    configuration: str
    quality: float
    citations: tuple[str, ...]
    citation_coverage: float
    stale_handling: StaleHandling
    turns: int
    retrieval_calls: int
    retrieval_stages: tuple[str, ...]
    returned_items: int
    returned_context: tuple[str, ...]
    deduplicated_items: int
    estimated_tokens: int
    latency_ms: float
    provider_usage: ProviderUsage | None = None
    cost: AuthoritativeCost | None = None
    schema_version: str = EVALUATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EVALUATION_SCHEMA_VERSION:
            raise ValueError("unsupported evaluation metric schema version")
        if not self.configuration or not 0 <= self.quality <= 1 or not 0 <= self.citation_coverage <= 1:
            raise ValueError("metrics require configuration, quality, and citation coverage between zero and one")
        if len(set(self.citations)) != len(self.citations):
            raise ValueError("metric citations must be deduplicated")
        if any(value < 0 for value in (self.turns, self.retrieval_calls, self.returned_items, self.deduplicated_items, self.estimated_tokens)):
            raise ValueError("metric counts cannot be negative")
        if not math.isfinite(self.latency_ms) or self.latency_ms < 0:
            raise ValueError("latency_ms must be a finite non-negative value")
        if self.cost is not None and not (
            self.provider_usage is not None
            and self.provider_usage.authoritative
            and self.cost.authoritative
        ):
            raise ValueError("cost is allowed only with authoritative provider usage")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OptimizationThresholds:
    """Savings never override required answer evidence or citation coverage."""

    require_full_citation_coverage: bool = True
    reject_quality_regression: bool = True
    require_expected_stale_handling: bool = True


@dataclass(frozen=True)
class TaskComparison:
    task_id: str
    baseline: EvaluationMetrics
    optimized: EvaluationMetrics
    passed: bool
    failures: tuple[str, ...]
    citation_coverage: float
    estimated_token_delta: int
    turn_delta: int
    retrieval_call_delta: int
    latency_delta_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "passed": self.passed,
            "failures": list(self.failures),
            "citation_coverage": self.citation_coverage,
            "estimated_token_delta": self.estimated_token_delta,
            "turn_delta": self.turn_delta,
            "retrieval_call_delta": self.retrieval_call_delta,
            "latency_delta_ms": self.latency_delta_ms,
            "baseline": self.baseline.to_dict(),
            "optimized": self.optimized.to_dict(),
        }


@dataclass(frozen=True)
class EvaluationReport:
    schema_version: str
    comparisons: tuple[TaskComparison, ...]
    authoritative_cost_per_successful_task: float | None

    @property
    def passed(self) -> bool:
        return bool(self.comparisons) and all(comparison.passed for comparison in self.comparisons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "passed": self.passed,
            "task_count": len(self.comparisons),
            "successful_task_count": sum(comparison.passed for comparison in self.comparisons),
            "authoritative_cost_per_successful_task": self.authoritative_cost_per_successful_task,
            "comparisons": [comparison.to_dict() for comparison in self.comparisons],
        }


EvaluationRunner = Callable[[RepresentativeTask], EvaluationMetrics]


def load_representative_tasks(path: Path) -> tuple[RepresentativeTask, ...]:
    """Load checked-in synthetic fixtures; no archive data is accepted here."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != EVALUATION_SCHEMA_VERSION:
        raise ValueError("unsupported representative task schema version")
    tasks = tuple(_task_from_mapping(item) for item in raw.get("tasks", []))
    expected_kinds = set(RepresentativeTaskKind)
    if {task.kind for task in tasks} != expected_kinds or len(tasks) != len(expected_kinds):
        raise ValueError("fixtures must contain each representative task kind exactly once")
    return tasks


def compare_configurations(
    tasks: Iterable[RepresentativeTask],
    baseline_runner: EvaluationRunner,
    optimized_runner: EvaluationRunner,
    thresholds: OptimizationThresholds = OptimizationThresholds(),
) -> EvaluationReport:
    """Run both configurations once for every task and return a stable report."""

    comparisons = tuple(
        evaluate_task(task, baseline_runner(task), optimized_runner(task), thresholds)
        for task in sorted(tasks, key=lambda item: item.task_id)
    )
    return EvaluationReport(
        schema_version=EVALUATION_SCHEMA_VERSION,
        comparisons=comparisons,
        authoritative_cost_per_successful_task=_cost_per_successful_task(comparisons),
    )


def evaluate_task(
    task: RepresentativeTask,
    baseline: EvaluationMetrics,
    optimized: EvaluationMetrics,
    thresholds: OptimizationThresholds = OptimizationThresholds(),
) -> TaskComparison:
    """Apply evidence-first gates; token/turn savings cannot make a run pass."""

    if baseline.configuration != "baseline" or optimized.configuration != "optimized":
        raise ValueError("comparison requires baseline and optimized metric configurations")
    failures: list[str] = []
    baseline_coverage = _citation_coverage(task, baseline)
    optimized_coverage = _citation_coverage(task, optimized)
    if baseline.citation_coverage != baseline_coverage or optimized.citation_coverage != optimized_coverage:
        failures.append("reported_citation_coverage_does_not_match_citations")
    if baseline_coverage < 1 or baseline.quality < task.minimum_quality:
        failures.append("baseline_does_not_meet_fixture_evidence_contract")
    if missing_terms := _missing_evidence_terms(task, baseline):
        failures.append(f"baseline_required_evidence_missing:{','.join(missing_terms)}")
    if optimized.quality < task.minimum_quality:
        failures.append("optimized_quality_below_fixture_threshold")
    if missing_terms := _missing_evidence_terms(task, optimized):
        failures.append(f"optimized_required_evidence_missing:{','.join(missing_terms)}")
    if thresholds.reject_quality_regression and optimized.quality < baseline.quality:
        failures.append("optimized_quality_regressed")
    if thresholds.require_full_citation_coverage and optimized_coverage < 1:
        failures.append("optimized_citation_coverage_incomplete")
    if optimized_coverage < baseline_coverage:
        failures.append("optimized_citation_coverage_regressed")
    if thresholds.require_expected_stale_handling and optimized.stale_handling != task.expected_stale_handling:
        failures.append("optimized_stale_handling_incorrect")
    return TaskComparison(
        task_id=task.task_id,
        baseline=baseline,
        optimized=optimized,
        passed=not failures,
        failures=tuple(failures),
        citation_coverage=optimized_coverage,
        estimated_token_delta=optimized.estimated_tokens - baseline.estimated_tokens,
        turn_delta=optimized.turns - baseline.turns,
        retrieval_call_delta=optimized.retrieval_calls - baseline.retrieval_calls,
        latency_delta_ms=optimized.latency_ms - baseline.latency_ms,
    )


def _task_from_mapping(value: Mapping[str, Any]) -> RepresentativeTask:
    return RepresentativeTask(
        task_id=str(value["task_id"]),
        kind=RepresentativeTaskKind(value["kind"]),
        query=str(value["query"]),
        scope_chat_ids=tuple(int(item) for item in value["scope_chat_ids"]),
        expected_citations=tuple(str(item) for item in value["expected_citations"]),
        required_evidence_terms=tuple(str(item) for item in value["required_evidence_terms"]),
        expected_stale_handling=StaleHandling(value["expected_stale_handling"]),
        minimum_quality=float(value.get("minimum_quality", 1)),
    )


def _citation_coverage(task: RepresentativeTask, metrics: EvaluationMetrics) -> float:
    expected = set(task.expected_citations)
    return len(expected & set(metrics.citations)) / len(expected)


def _missing_evidence_terms(task: RepresentativeTask, metrics: EvaluationMetrics) -> tuple[str, ...]:
    rendered_context = "\n".join(metrics.returned_context).casefold()
    return tuple(term for term in task.required_evidence_terms if term.casefold() not in rendered_context)


def _cost_per_successful_task(comparisons: tuple[TaskComparison, ...]) -> float | None:
    successful = [comparison for comparison in comparisons if comparison.passed]
    if not successful or any(comparison.optimized.cost is None for comparison in successful):
        return None
    currencies = {comparison.optimized.cost.currency for comparison in successful if comparison.optimized.cost}
    if len(currencies) != 1:
        return None
    return sum(comparison.optimized.cost.amount for comparison in successful if comparison.optimized.cost) / len(successful)
