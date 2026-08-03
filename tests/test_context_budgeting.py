from __future__ import annotations

import json

import pytest

from tg_recall.context_budgeting import (
    DEFAULT_SAFETY_MARGIN,
    ActualUsage,
    CalibrationFixture,
    ConservativeUtf8JsonTokenCounter,
    EvidenceItem,
    ExactTokenizerCandidate,
    RetrievalBudgets,
    RetrievalOutcome,
    RetrievalStage,
    benchmark_exact_tokenizer,
    benchmark_available_exact_tokenizers,
    build_bounded_retrieval,
    calibrate_counter,
    canonical_json_bytes,
    deduplicate_evidence_items,
    progressive_stage_plan,
)


@pytest.fixture
def multilingual_fixtures() -> tuple[CalibrationFixture, ...]:
    return (
        CalibrationFixture("russian", {"text": "Оплата согласована, дедлайн — пятница."}, 22),
        CalibrationFixture("english", {"text": "Payment is approved; the deadline is Friday."}, 16),
        CalibrationFixture("emoji", {"text": "✅ Отправлено 🚀"}, 9),
        CalibrationFixture("url", {"citation": "tg://chat/-100123/message/42", "url": "https://example.test/a?source=tg-recall"}, 25),
        CalibrationFixture("transcript", {"transcript": "[00:01] Привет. [00:04] We need the contract."}, 30),
        CalibrationFixture("json", {"metadata": {"chat_id": -100123, "reply": 42}, "text": "данные"}, 23),
    )


def test_fallback_counts_exact_canonical_utf8_json_and_is_calibratable(multilingual_fixtures: tuple[CalibrationFixture, ...]) -> None:
    counter = ConservativeUtf8JsonTokenCounter()
    payload = {"text": "Привет 👋", "citation": "tg://chat/1/message/2"}

    assert counter.count(canonical_json_bytes(payload)) == len(canonical_json_bytes(payload))
    report = calibrate_counter(counter, multilingual_fixtures)
    assert [item.fixture for item in report.measurements] == [item.name for item in multilingual_fixtures]
    assert report.maximum_underestimate == 0
    assert report.as_json()["counter_version"] == "1"


def test_benchmark_boundary_accepts_only_caller_verified_exact_counter(multilingual_fixtures: tuple[CalibrationFixture, ...]) -> None:
    report = benchmark_exact_tokenizer(
        ConservativeUtf8JsonTokenCounter(),
        multilingual_fixtures,
        exact_count=lambda payload: max(1, len(payload) // 3),
    )

    assert report.maximum_underestimate == 0
    assert all(item.reference_tokens == max(1, len(multilingual_fixtures[index].serialized()) // 3) for index, item in enumerate(report.measurements))


def test_optional_tokenizer_calibration_uses_sanitized_fixtures_without_discovery_or_network() -> None:
    no_candidate = benchmark_available_exact_tokenizers()
    candidate = ExactTokenizerCandidate("verified-local-bpe", "test-v1", lambda payload: max(1, len(payload) // 3))
    with_candidate = benchmark_available_exact_tokenizers((candidate,))

    assert [item.fixture for item in no_candidate.fallback.measurements] == [
        "russian", "english", "emoji", "url", "transcript", "json"
    ]
    assert no_candidate.exact_tokenizer_available is False
    assert no_candidate.as_json()["fallback_authoritative_estimate"] is True
    assert with_candidate.exact_tokenizer_available is True
    assert with_candidate.exact_candidates[0].candidate == "verified-local-bpe"
    assert with_candidate.exact_candidates[0].report.maximum_underestimate == 0


def test_retrieval_budget_reserves_fifteen_percent_and_keeps_actual_usage_separate() -> None:
    budgets = RetrievalBudgets(token_budget=1_000)
    actual = ActualUsage(source="host", model="gpt-5.6", input_tokens=777, output_tokens=30)
    result = build_bounded_retrieval(
        [EvidenceItem("tg://chat/1/message/1", "short cited evidence")],
        budgets,
        actual_usage=actual,
    )
    payload = result.as_json()

    assert budgets.safety_margin == DEFAULT_SAFETY_MARGIN
    assert payload["accounting"]["usable_payload_budget"] == 850
    assert payload["accounting"]["estimated_tokens"] != 777
    assert payload["accounting"]["actual_usage"] == {"source": "host", "model": "gpt-5.6", "input_tokens": 777, "output_tokens": 30}


def test_tiny_budget_keeps_the_full_safety_reserve_and_reports_minimum_payload_exhaustion() -> None:
    counter = ConservativeUtf8JsonTokenCounter()
    budgets = RetrievalBudgets(token_budget=1)
    result = build_bounded_retrieval([EvidenceItem("tg://chat/1/message/1", "evidence")], budgets, counter=counter)

    assert budgets.usable_payload_budget == 0
    assert result.outcome == RetrievalOutcome.BUDGET_EXHAUSTED
    assert result.reason == "minimum_payload_exceeds_usable_budget"
    assert result.accounting.estimated_tokens == counter.count(canonical_json_bytes({"items": []}))
    assert result.accounting.estimated_tokens > result.accounting.usable_payload_budget


def test_reused_or_stale_provenance_never_hides_budget_exhaustion_or_marks_it_sufficient() -> None:
    budgets = RetrievalBudgets(token_budget=100)
    evidence = [EvidenceItem("tg://chat/1/message/1", "very long evidence " * 100)]

    reused = build_bounded_retrieval(evidence, budgets, reused_evidence=True)
    stale = build_bounded_retrieval(evidence, budgets, stale=True, retries=2)

    assert reused.outcome != RetrievalOutcome.REUSED
    assert reused.telemetry.sufficient is False
    assert stale.outcome == RetrievalOutcome.BUDGET_EXHAUSTED
    assert stale.telemetry.sufficient is False


def test_deduplicate_windows_before_payload_accounting_and_preserve_hit_references() -> None:
    items = [
        EvidenceItem("tg://chat/1/message/2", "short", hit_citations=("tg://chat/1/message/10",)),
        EvidenceItem("tg://chat/1/message/2", "longer source context", hit_citations=("tg://chat/1/message/11",)),
        EvidenceItem("tg://chat/1/message/3", "other"),
    ]

    deduplicated = deduplicate_evidence_items(items)
    result = build_bounded_retrieval(items, RetrievalBudgets(token_budget=2_000))

    assert len(deduplicated) == 2
    assert deduplicated[0].text == "longer source context"
    assert set(deduplicated[0].hit_citations) == {
        "tg://chat/1/message/2",
        "tg://chat/1/message/10",
        "tg://chat/1/message/11",
    }
    assert result.telemetry.deduplicated_items == 2


def test_deduplication_preserves_caller_ranking_and_item_limit_is_incomplete() -> None:
    result = build_bounded_retrieval(
        [
            EvidenceItem("tg://chat/1/message/99", "highest ranked result"),
            EvidenceItem("tg://chat/1/message/1", "lower ranked result"),
        ],
        RetrievalBudgets(token_budget=1_000, item_limit=1),
    )

    assert result.items[0]["citation"] == "tg://chat/1/message/99"
    assert result.outcome == RetrievalOutcome.INCOMPLETE
    assert result.reason == "item_limit_reached"
    assert result.telemetry.sufficient is False


def test_oversized_first_item_is_explicitly_truncated_without_losing_its_citation() -> None:
    result = build_bounded_retrieval(
        [EvidenceItem("tg://chat/9/message/9", "я" * 1_000, metadata={"chat_id": 9})],
        RetrievalBudgets(token_budget=300),
    )

    assert result.outcome == RetrievalOutcome.INCOMPLETE
    assert result.accounting.truncated is True
    assert result.items[0]["citation"] == "tg://chat/9/message/9"
    assert result.items[0]["truncated"] is True
    assert result.accounting.estimated_tokens <= result.accounting.usable_payload_budget


def test_work_budget_exhaustion_is_stable_and_does_not_serialize_candidates() -> None:
    result = build_bounded_retrieval(
        [EvidenceItem("tg://chat/1/message/1", "evidence")],
        RetrievalBudgets(retry_budget=0, tool_call_budget=0),
        stage=RetrievalStage.CATALOG,
        retries=1,
    )
    encoded = json.dumps(result.as_json(), sort_keys=True)

    assert result.outcome == RetrievalOutcome.BUDGET_EXHAUSTED
    assert result.reason == "work_budget_exhausted"
    assert result.items == ()
    assert result.accounting.estimated_tokens == ConservativeUtf8JsonTokenCounter().count(canonical_json_bytes({"items": []}))
    assert '"outcome": "budget_exhausted"' in encoded


def test_progressive_ladder_is_ordered_and_cannot_include_media_without_an_explicit_request() -> None:
    budgets = RetrievalBudgets(stage_budget=5)

    assert progressive_stage_plan(budgets) == (
        RetrievalStage.REUSED_EVIDENCE,
        RetrievalStage.CATALOG,
        RetrievalStage.RAW,
        RetrievalStage.EXPANSION,
    )
    assert progressive_stage_plan(budgets, include_media=True)[-1] == RetrievalStage.MEDIA


def test_stale_and_reused_results_have_stable_distinct_outcomes() -> None:
    evidence = [EvidenceItem("tg://chat/1/message/1", "cited evidence")]
    budgets = RetrievalBudgets(token_budget=1_000)

    stale = build_bounded_retrieval(evidence, budgets, stale=True)
    reused = build_bounded_retrieval(evidence, budgets, reused_evidence=True)

    assert stale.as_json()["outcome"] == RetrievalOutcome.STALE.value
    assert reused.as_json()["outcome"] == RetrievalOutcome.REUSED.value
