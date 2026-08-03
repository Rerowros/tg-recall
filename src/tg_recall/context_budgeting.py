"""Deterministic, provider-neutral context budgeting primitives.

This module owns *retrieval payload* accounting only.  It deliberately knows
nothing about SQLite, CLI sessions, model billing, or a host's hidden context:
callers give it already-authorized evidence and can persist the returned
stable JSON wherever their profile-local policy permits.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


TOKEN_COUNTER_SCHEMA_VERSION = 1
DEFAULT_SAFETY_MARGIN = 0.15


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize the exact UTF-8 JSON representation used for accounting."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


@runtime_checkable
class TokenCounter(Protocol):
    """Versioned boundary for local token estimators or verified tokenizers."""

    name: str
    version: str

    def count(self, serialized_payload: bytes) -> int:
        """Return tokens for this exact UTF-8 serialized payload."""


@dataclass(frozen=True)
class ConservativeUtf8JsonTokenCounter:
    """Safe fallback when no verified local tokenizer mapping is available.

    A byte is the minimum BPE unit, so treating each serialized UTF-8 byte as
    one token is intentionally conservative for mixed Russian/English text,
    citations, JSON punctuation, URLs and emoji.  It is not billing data and
    callers must expose that distinction through :class:`TokenAccounting`.
    """

    name: str = "utf8-json-conservative"
    version: str = "1"

    def count(self, serialized_payload: bytes) -> int:
        if not isinstance(serialized_payload, bytes):
            raise TypeError("serialized_payload must be bytes")
        return len(serialized_payload)


@dataclass(frozen=True)
class CalibrationFixture:
    """Sanitized payload and optional independently measured reference count."""

    name: str
    payload: Any
    reference_tokens: int

    def serialized(self) -> bytes:
        if self.reference_tokens < 0:
            raise ValueError("reference_tokens must not be negative")
        return canonical_json_bytes(self.payload)


SANITIZED_CALIBRATION_FIXTURES: tuple[CalibrationFixture, ...] = (
    CalibrationFixture("russian", {"text": "Оплата согласована, дедлайн — пятница."}, 22),
    CalibrationFixture("english", {"text": "Payment is approved; the deadline is Friday."}, 16),
    CalibrationFixture("emoji", {"text": "✅ Отправлено 🚀"}, 9),
    CalibrationFixture(
        "url",
        {"citation": "tg://chat/-100123/message/42", "url": "https://example.test/a?source=tg-recall"},
        25,
    ),
    CalibrationFixture("transcript", {"transcript": "[00:01] Привет. [00:04] We need the contract."}, 30),
    CalibrationFixture("json", {"metadata": {"chat_id": -100123, "reply": 42}, "text": "данные"}, 23),
)


@dataclass(frozen=True)
class CalibrationMeasurement:
    fixture: str
    estimated_tokens: int
    reference_tokens: int
    error_tokens: int
    error_ratio: float | None


@dataclass(frozen=True)
class CalibrationReport:
    counter: str
    counter_version: str
    measurements: tuple[CalibrationMeasurement, ...]

    @property
    def maximum_underestimate(self) -> int:
        return max((max(0, item.reference_tokens - item.estimated_tokens) for item in self.measurements), default=0)

    @property
    def maximum_overestimate(self) -> int:
        return max((max(0, item.estimated_tokens - item.reference_tokens) for item in self.measurements), default=0)

    def as_json(self) -> dict[str, Any]:
        return {
            "counter": self.counter,
            "counter_version": self.counter_version,
            "maximum_overestimate": self.maximum_overestimate,
            "maximum_underestimate": self.maximum_underestimate,
            "measurements": [asdict(item) for item in self.measurements],
        }


def calibrate_counter(counter: TokenCounter, fixtures: Iterable[CalibrationFixture]) -> CalibrationReport:
    """Measure a counter against externally supplied sanitized references."""

    measurements: list[CalibrationMeasurement] = []
    for fixture in fixtures:
        estimated = counter.count(fixture.serialized())
        reference = fixture.reference_tokens
        error = estimated - reference
        measurements.append(
            CalibrationMeasurement(
                fixture=fixture.name,
                estimated_tokens=estimated,
                reference_tokens=reference,
                error_tokens=error,
                error_ratio=None if reference == 0 else error / reference,
            )
        )
    return CalibrationReport(counter.name, counter.version, tuple(measurements))


def benchmark_exact_tokenizer(
    counter: TokenCounter,
    fixtures: Iterable[CalibrationFixture],
    exact_count: Callable[[bytes], int],
) -> CalibrationReport:
    """Benchmark a candidate local tokenizer without adding a tokenizer dependency.

    ``exact_count`` must be supplied only by a caller that has verified the
    model/tokenizer version locally.  The function has no network behavior.
    """

    measured = tuple(
        CalibrationFixture(item.name, item.payload, exact_count(item.serialized())) for item in fixtures
    )
    return calibrate_counter(counter, measured)


@dataclass(frozen=True)
class ExactTokenizerCandidate:
    """A caller-verified, already-local tokenizer used only for calibration.

    This module intentionally does not import, install, download, or map a
    tokenizer to any Codex model. A host may inject a known local tokenizer
    after independently verifying that mapping and version.
    """

    name: str
    version: str
    exact_count: Callable[[bytes], int] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.name or not self.version or not callable(self.exact_count):
            raise ValueError("exact tokenizer candidate needs a name, version, and local counter")


@dataclass(frozen=True)
class ExactTokenizerCalibration:
    candidate: str
    candidate_version: str
    report: CalibrationReport

    def as_json(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate,
            "candidate_version": self.candidate_version,
            "report": self.report.as_json(),
        }


@dataclass(frozen=True)
class OptionalTokenizerBenchmark:
    """Offline calibration result; fallback accounting remains authoritative."""

    fallback: CalibrationReport
    exact_candidates: tuple[ExactTokenizerCalibration, ...] = ()

    @property
    def exact_tokenizer_available(self) -> bool:
        return bool(self.exact_candidates)

    def as_json(self) -> dict[str, Any]:
        return {
            "exact_candidates": [item.as_json() for item in self.exact_candidates],
            "exact_tokenizer_available": self.exact_tokenizer_available,
            "fallback": self.fallback.as_json(),
            "fallback_authoritative_estimate": True,
        }


def benchmark_available_exact_tokenizers(
    candidates: Iterable[ExactTokenizerCandidate] = (),
    *,
    fixtures: Iterable[CalibrationFixture] = SANITIZED_CALIBRATION_FIXTURES,
    fallback: TokenCounter | None = None,
) -> OptionalTokenizerBenchmark:
    """Calibrate injected local candidates without dependency or network I/O.

    The default contains no candidate discovery: discovering or initializing a
    third-party tokenizer can fetch resources or imply a model mapping that
    tg-recall cannot prove. With no injected candidate, the UTF-8 fallback is
    the explicit and authoritative payload estimate.
    """

    fixture_set = tuple(fixtures)
    fallback_counter = fallback or ConservativeUtf8JsonTokenCounter()
    reports = tuple(
        ExactTokenizerCalibration(
            candidate=item.name,
            candidate_version=item.version,
            report=benchmark_exact_tokenizer(fallback_counter, fixture_set, item.exact_count),
        )
        for item in candidates
    )
    return OptionalTokenizerBenchmark(calibrate_counter(fallback_counter, fixture_set), reports)


class RetrievalOutcome(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    STALE = "stale"
    REUSED = "reused"
    BUDGET_EXHAUSTED = "budget_exhausted"


class RetrievalStage(StrEnum):
    REUSED_EVIDENCE = "reused_evidence"
    CATALOG = "catalog"
    RAW = "raw"
    EXPANSION = "expansion"
    MEDIA = "media"


@dataclass(frozen=True)
class RetrievalBudgets:
    """Explicit host-work limits; none authorizes an unbounded archive scan."""

    item_limit: int = 8
    context_radius: int = 2
    token_budget: int = 4_000
    stage_budget: int = 3
    retry_budget: int = 1
    tool_call_budget: int = 2
    safety_margin: float = DEFAULT_SAFETY_MARGIN

    def __post_init__(self) -> None:
        if self.item_limit < 1 or self.context_radius < 0 or self.token_budget < 1:
            raise ValueError("item_limit and token_budget must be positive; context_radius must not be negative")
        if self.stage_budget < 1 or self.retry_budget < 0 or self.tool_call_budget < 0:
            raise ValueError("stage_budget must be positive; retry_budget and tool_call_budget must not be negative")
        if not 0 <= self.safety_margin < 1:
            raise ValueError("safety_margin must be in [0, 1)")

    @property
    def usable_payload_budget(self) -> int:
        # Do not round a 15% reserve away for tiny caller budgets.  A zero
        # result means even the minimum serialized payload cannot be admitted;
        # the caller receives an explicit budget-exhausted result instead.
        return math.floor(self.token_budget * (1 - self.safety_margin))

    def as_json(self) -> dict[str, Any]:
        return {
            "context_radius": self.context_radius,
            "item_limit": self.item_limit,
            "retry_budget": self.retry_budget,
            "safety_margin": self.safety_margin,
            "stage_budget": self.stage_budget,
            "token_budget": self.token_budget,
            "tool_call_budget": self.tool_call_budget,
            "usable_payload_budget": self.usable_payload_budget,
        }


def progressive_stage_plan(budgets: RetrievalBudgets, *, include_media: bool = False) -> tuple[RetrievalStage, ...]:
    """Return the bounded, ordered retrieval ladder for an orchestration host.

    Media is excluded unless the caller explicitly requested it.  The returned
    plan is a pure advisory value: it cannot cause a download or a scan.
    """

    stages = [
        RetrievalStage.REUSED_EVIDENCE,
        RetrievalStage.CATALOG,
        RetrievalStage.RAW,
        RetrievalStage.EXPANSION,
    ]
    if include_media:
        stages.append(RetrievalStage.MEDIA)
    return tuple(stages[: budgets.stage_budget])


@dataclass(frozen=True)
class EvidenceItem:
    """One already-authorized evidence body, with its citation kept attached."""

    citation: str
    text: str
    source_type: str = "message"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    hit_citations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.citation or not isinstance(self.citation, str):
            raise ValueError("evidence citation is required")
        if not isinstance(self.text, str) or not self.text:
            raise ValueError("evidence text is required")
        if not self.source_type:
            raise ValueError("evidence source_type is required")

    def payload(self, *, text: str | None = None, truncated: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "citation": self.citation,
            "metadata": dict(self.metadata),
            "source_type": self.source_type,
            "text": self.text if text is None else text,
        }
        if self.hit_citations:
            result["hit_citations"] = list(self.hit_citations)
        if truncated:
            result["truncated"] = True
        return result


def deduplicate_evidence_items(items: Iterable[EvidenceItem]) -> list[EvidenceItem]:
    """Merge overlapping source windows by citation before JSON accounting.

    The longest evidence body wins, while every discovered hit reference is
    retained.  Conflicting metadata is intentionally not blended: it would
    create claims no source returned.  The first deterministic candidate owns
    it instead.
    """

    grouped: dict[str, EvidenceItem] = {}
    for item in items:
        existing = grouped.get(item.citation)
        if existing is None:
            grouped[item.citation] = item
            continue
        hit_citations = tuple(sorted({*existing.hit_citations, *item.hit_citations, existing.citation, item.citation}))
        winner = item if len(item.text.encode("utf-8")) > len(existing.text.encode("utf-8")) else existing
        grouped[item.citation] = EvidenceItem(
            citation=winner.citation,
            text=winner.text,
            source_type=winner.source_type,
            metadata=winner.metadata,
            hit_citations=hit_citations,
        )
    # ``dict`` preserves first-seen insertion order, which is the caller's
    # relevance ranking.  Never replace it with citation sorting: an older
    # message ID is not evidence that a result is more relevant.
    return list(grouped.values())


@dataclass(frozen=True)
class ActualUsage:
    """Host/provider reported usage, deliberately separate from estimates."""

    source: str
    model: str
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None

    def __post_init__(self) -> None:
        if not self.source or not self.model:
            raise ValueError("actual usage source and model are required")
        if any(value is not None and value < 0 for value in (self.input_tokens, self.cached_input_tokens, self.output_tokens, self.reasoning_tokens)):
            raise ValueError("actual usage values must not be negative")

    def as_json(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class RetrievalTelemetry:
    stage: RetrievalStage
    stages_used: tuple[RetrievalStage, ...] = ()
    retries: int = 0
    tool_calls: int = 0
    returned_items: int = 0
    deduplicated_items: int = 0
    reused_evidence: bool = False
    sufficient: bool = True

    def __post_init__(self) -> None:
        if self.retries < 0 or self.tool_calls < 0 or self.returned_items < 0 or self.deduplicated_items < 0:
            raise ValueError("retrieval telemetry counts must not be negative")

    def as_json(self) -> dict[str, Any]:
        return {
            "deduplicated_items": self.deduplicated_items,
            "returned_items": self.returned_items,
            "retries": self.retries,
            "reused_evidence": self.reused_evidence,
            "stage": self.stage.value,
            "stages_used": [item.value for item in self.stages_used],
            "sufficient": self.sufficient,
            "tool_calls": self.tool_calls,
        }


@dataclass(frozen=True)
class TokenAccounting:
    token_budget: int
    usable_payload_budget: int
    estimated_tokens: int
    counter: str
    counter_version: str
    safety_margin: float
    truncated: bool
    actual_usage: ActualUsage | None = None

    def as_json(self) -> dict[str, Any]:
        result = {
            "counter": self.counter,
            "counter_version": self.counter_version,
            "estimated_tokens": self.estimated_tokens,
            "safety_margin": self.safety_margin,
            "token_budget": self.token_budget,
            "truncated": self.truncated,
            "usable_payload_budget": self.usable_payload_budget,
        }
        if self.actual_usage is not None:
            result["actual_usage"] = self.actual_usage.as_json()
        return result


@dataclass(frozen=True)
class BoundedRetrievalResult:
    outcome: RetrievalOutcome
    items: tuple[dict[str, Any], ...]
    accounting: TokenAccounting
    telemetry: RetrievalTelemetry
    reason: str | None = None

    def as_json(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "accounting": self.accounting.as_json(),
            "items": list(self.items),
            "outcome": self.outcome.value,
            "telemetry": self.telemetry.as_json(),
        }
        if self.reason is not None:
            result["reason"] = self.reason
        return result


def build_bounded_retrieval(
    candidates: Sequence[EvidenceItem],
    budgets: RetrievalBudgets,
    *,
    stage: RetrievalStage = RetrievalStage.RAW,
    counter: TokenCounter | None = None,
    retries: int = 0,
    tool_calls: int = 0,
    reused_evidence: bool = False,
    stale: bool = False,
    actual_usage: ActualUsage | None = None,
) -> BoundedRetrievalResult:
    """Deduplicate, exactly serialize, and admit only bounded evidence.

    An oversized first item is shortened by UTF-8-safe character boundaries;
    its citation and metadata remain intact.  Later oversized items are not
    silently partially copied.  This lets callers widen explicitly instead of
    turning a small retrieval into an accidental archive export.
    """

    selected_counter = counter or ConservativeUtf8JsonTokenCounter()
    if retries > budgets.retry_budget or tool_calls > budgets.tool_call_budget:
        return _empty_result(
            RetrievalOutcome.BUDGET_EXHAUSTED,
            budgets,
            stage,
            selected_counter,
            retries=retries,
            tool_calls=tool_calls,
            reason="work_budget_exhausted",
            actual_usage=actual_usage,
            reused_evidence=reused_evidence,
        )
    empty_payload_tokens = _count_items((), selected_counter)
    if empty_payload_tokens > budgets.usable_payload_budget:
        return _empty_result(
            RetrievalOutcome.BUDGET_EXHAUSTED,
            budgets,
            stage,
            selected_counter,
            retries=retries,
            tool_calls=tool_calls,
            reason="minimum_payload_exceeds_usable_budget",
            actual_usage=actual_usage,
            reused_evidence=reused_evidence,
        )
    deduplicated = deduplicate_evidence_items(candidates)
    selected: list[dict[str, Any]] = []
    truncated = False
    item_limit_reached = False
    for item in deduplicated:
        if len(selected) >= budgets.item_limit:
            item_limit_reached = True
            break
        candidate = item.payload()
        if _count_items(selected + [candidate], selected_counter) <= budgets.usable_payload_budget:
            selected.append(candidate)
            continue
        if not selected:
            shortened = _truncate_first_item(item, budgets.usable_payload_budget, selected_counter)
            if shortened is not None:
                selected.append(shortened)
                truncated = True
        break
    estimate = _count_items(selected, selected_counter)
    exhausted = bool(deduplicated) and (not selected or len(selected) < min(len(deduplicated), budgets.item_limit))
    outcome = (
        RetrievalOutcome.BUDGET_EXHAUSTED
        if exhausted
        else RetrievalOutcome.INCOMPLETE
        if truncated or not deduplicated or item_limit_reached
        else RetrievalOutcome.STALE
        if stale
        else RetrievalOutcome.REUSED
        if reused_evidence
        else RetrievalOutcome.COMPLETE
    )
    telemetry = RetrievalTelemetry(
        stage=stage,
        stages_used=(stage,),
        retries=retries,
        tool_calls=tool_calls,
        returned_items=len(selected),
        deduplicated_items=len(deduplicated),
        reused_evidence=reused_evidence,
        sufficient=outcome in {RetrievalOutcome.COMPLETE, RetrievalOutcome.REUSED},
    )
    accounting = TokenAccounting(
        token_budget=budgets.token_budget,
        usable_payload_budget=budgets.usable_payload_budget,
        estimated_tokens=estimate,
        counter=selected_counter.name,
        counter_version=selected_counter.version,
        safety_margin=budgets.safety_margin,
        truncated=truncated,
        actual_usage=actual_usage,
    )
    reason = (
        "payload_budget_exhausted"
        if outcome == RetrievalOutcome.BUDGET_EXHAUSTED
        else "payload_truncated"
        if truncated
        else "item_limit_reached"
        if item_limit_reached
        else "no_evidence"
        if not deduplicated
        else None
    )
    return BoundedRetrievalResult(outcome, tuple(selected), accounting, telemetry, reason)


def _empty_result(
    outcome: RetrievalOutcome,
    budgets: RetrievalBudgets,
    stage: RetrievalStage,
    counter: TokenCounter,
    *,
    retries: int,
    tool_calls: int,
    reason: str,
    actual_usage: ActualUsage | None,
    reused_evidence: bool,
) -> BoundedRetrievalResult:
    return BoundedRetrievalResult(
        outcome=outcome,
        items=(),
        accounting=TokenAccounting(
            budgets.token_budget,
            budgets.usable_payload_budget,
            _count_items((), counter),
            counter.name,
            counter.version,
            budgets.safety_margin,
            False,
            actual_usage,
        ),
        telemetry=RetrievalTelemetry(stage, (stage,), retries, tool_calls, 0, 0, reused_evidence, False),
        reason=reason,
    )


def _count_items(items: Sequence[Mapping[str, Any]], counter: TokenCounter) -> int:
    return counter.count(canonical_json_bytes({"items": list(items)}))


def _truncate_first_item(item: EvidenceItem, usable_budget: int, counter: TokenCounter) -> dict[str, Any] | None:
    text = item.text
    low, high = 0, len(text)
    best: dict[str, Any] | None = None
    while low <= high:
        middle = (low + high) // 2
        candidate = item.payload(text=text[:middle], truncated=True)
        if _count_items([candidate], counter) <= usable_budget:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best
