from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from tg_recall.llm_answers import SynthesisPolicy, run_synthesis
from tg_recall.openai_responses import OpenAIResponsesProvider


def policy() -> SynthesisPolicy:
    return SynthesisPolicy(
        provider="openai-responses",
        model="gpt-test",
        credentials_configured=True,
        external_llm_enabled=True,
        ai_access_enabled=True,
        allowed_chat_ids=(10,),
        allowed_data_classes=frozenset({"message_text", "metadata"}),
        max_evidence_items=2,
        token_budget=1_000,
    )


def evidence() -> dict[str, object]:
    return {
        "chat_id": 10,
        "message_id": 7,
        "timestamp": "2026-08-03T10:00:00+00:00",
        "chat_title": "Synthetic",
        "text": "The synthetic deadline is Friday.",
    }


class FakeResponses:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def fake_client(result: object) -> tuple[SimpleNamespace, FakeResponses]:
    responses = FakeResponses(result)
    return SimpleNamespace(responses=responses), responses


def valid_response() -> SimpleNamespace:
    return SimpleNamespace(
        output_text=json.dumps({"answer": "Friday.", "evidence_ids": ["e1"]}),
        usage=SimpleNamespace(input_tokens=13, output_tokens=4),
    )


def test_responses_adapter_is_structured_stateless_and_tool_free() -> None:
    client, responses = fake_client(valid_response())
    provider = OpenAIResponsesProvider(api_key="sk-do-not-log", timeout_seconds=12, max_output_tokens=321, client=client)
    audits = []
    answer = run_synthesis("When?", [evidence()], policy(), provider, audit_sink=audits.append)
    assert answer.mode == "synthesized"
    assert answer.citations == ("tg://chat/10/message/7",)
    assert answer.usage is not None and answer.usage.input_tokens == 13
    call = responses.calls[0]
    assert call["store"] is False
    assert call["timeout"] == 12
    assert call["max_output_tokens"] == 321
    assert "tools" not in call and "tool_choice" not in call and "functions" not in call
    assert call["text"] == {"format": {"type": "json_schema", "name": "tg_recall_cited_answer", "strict": True, "schema": {"type": "object", "properties": {"answer": {"type": "string"}, "evidence_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["answer", "evidence_ids"], "additionalProperties": False}}}
    assert "sk-do-not-log" not in json.dumps(call)
    assert "sk-do-not-log" not in repr(provider)
    assert "sk-do-not-log" not in json.dumps(audits[0].as_dict())
    assert provider.__dict__["_api_key"] is None


@pytest.mark.parametrize("value", [0, -1, 1.5, True])
def test_output_budget_requires_positive_integer(value: object) -> None:
    with pytest.raises(ValueError, match="max_output_tokens"):
        OpenAIResponsesProvider(api_key="sk-secret", max_output_tokens=value)  # type: ignore[arg-type]


def test_default_output_budget_is_bounded_and_key_is_dropped_after_lazy_client_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, object]] = []
    client, responses = fake_client(valid_response())

    class FakeOpenAI:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)
            self.responses = client.responses

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    provider = OpenAIResponsesProvider(api_key="sk-secret")
    answer = run_synthesis("When?", [evidence()], policy(), provider)
    assert answer.mode == "synthesized"
    assert created == [{"api_key": "sk-secret", "timeout": 30.0}]
    assert responses.calls[0]["max_output_tokens"] == 800
    assert provider.__dict__["_api_key"] is None
    assert "sk-secret" not in repr(provider)


@pytest.mark.parametrize(
    ("error", "expected"),
    [(TimeoutError(), "timeout"), (OSError(), "network"), (type("RateLimitError", (Exception,), {})(), "rate_limit")],
)
def test_transport_failures_are_sanitized_and_fall_back(error: Exception, expected: str) -> None:
    client, _ = fake_client(error)
    audits = []
    answer = run_synthesis("When?", [evidence()], policy(), OpenAIResponsesProvider(api_key="sk-secret", client=client), audit_sink=audits.append)
    assert answer.mode == "extractive"
    assert answer.provider_status == "provider_unavailable"
    assert audits[0].failure_class == expected
    assert "sk-secret" not in json.dumps(audits[0].as_dict())


@pytest.mark.parametrize("output", ["not json", json.dumps({"answer": "x", "evidence_ids": ["e1"], "extra": True}), json.dumps({"answer": "x", "evidence_ids": [1]})])
def test_invalid_structured_output_falls_back(output: str) -> None:
    client, _ = fake_client(SimpleNamespace(output_text=output, usage=None))
    answer = run_synthesis("When?", [evidence()], policy(), OpenAIResponsesProvider(api_key="sk-secret", client=client))
    assert answer.mode == "extractive"
    assert answer.provider_status == "invalid_response"


def test_optional_sdk_import_is_lazy_and_missing_sdk_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAIResponsesProvider(api_key="sk-secret")
    monkeypatch.setitem(sys.modules, "openai", None)
    audits = []
    answer = run_synthesis("When?", [evidence()], policy(), provider, audit_sink=audits.append)
    assert answer.mode == "extractive"
    assert audits[0].failure_class == "unavailable"
    assert "sk-secret" not in json.dumps(audits[0].as_dict())
