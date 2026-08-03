from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from tg_recall.assistant import ArchiveAssistant
from tg_recall.cli import main
from tg_recall.config import AIAccessPolicy, AppConfig, LLMConfig, ProviderPolicy, load_config, save_config, set_config_value
from tg_recall.llm_answers import (
    DeterministicMockProvider,
    ProviderFailure,
    ProviderResponse,
    ProviderUsage,
    SynthesisPolicy,
    SynthesisPolicyDenied,
    bound_evidence,
    run_synthesis,
    serialize_provider_request,
)
from tg_recall.mcp_server import ReadOnlyMCPServer
from tg_recall.models import ChatRecord, MessageRecord, SearchFilters
from tg_recall.storage import Database


def policy(**overrides: object) -> SynthesisPolicy:
    values: dict[str, object] = {
        "provider": "mock",
        "model": "mock-1",
        "credentials_configured": True,
        "external_llm_enabled": True,
        "ai_access_enabled": True,
        "allowed_chat_ids": (10,),
        "allowed_data_classes": frozenset({"message_text", "metadata"}),
        "max_evidence_items": 2,
        "token_budget": 1_000,
    }
    values.update(overrides)
    return SynthesisPolicy(**values)


def evidence(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "chat_id": 10,
        "message_id": 7,
        "timestamp": "2026-08-03T10:00:00+00:00",
        "chat_title": "Budget",
        "text": "The deadline is Friday.",
    }
    values.update(overrides)
    return values


def test_policy_denial_does_not_construct_request_or_call_provider() -> None:
    provider = DeterministicMockProvider()
    audits = []
    with pytest.raises(SynthesisPolicyDenied):
        run_synthesis("when?", [evidence()], policy(external_llm_enabled=False), provider, audit_sink=audits.append)
    assert provider.calls == []
    assert audits[0].provider_status == "policy_denied"
    assert audits[0].evidence_count == 0


@pytest.mark.parametrize("allowed", [frozenset(), frozenset({"metadata"}), frozenset({"message_text"}), frozenset({"message_text", "metadata", "media"})])
def test_policy_requires_explicit_permission_for_every_serialized_data_class(allowed: frozenset[str]) -> None:
    provider = DeterministicMockProvider()
    with pytest.raises(SynthesisPolicyDenied):
        run_synthesis("when?", [evidence()], policy(allowed_data_classes=allowed), provider)
    assert provider.calls == []


def test_bounded_evidence_whitelists_private_fields_and_scope() -> None:
    safe = bound_evidence(
        [
            evidence(session_path="C:/private.session", credentials={"api_key": "secret"}, db_path="archive.sqlite3"),
            evidence(chat_id=999, message_id=8),
        ],
        policy(),
    )
    assert len(safe) == 1
    assert safe[0].citation == "tg://chat/10/message/7"
    serialized = json.dumps(safe[0].__dict__)
    assert "secret" not in serialized
    assert "private.session" not in serialized
    assert "archive.sqlite3" not in serialized


def test_valid_mock_synthesis_resolves_only_supplied_tg_citations() -> None:
    provider = DeterministicMockProvider(text="Friday.")
    answer = run_synthesis("when?", [evidence()], policy(), provider)
    assert answer.mode == "synthesized"
    assert answer.citations == ("tg://chat/10/message/7",)
    payload = answer.as_dict()
    assert payload["synthesis"]["provider_status"] == "synthesized"
    assert payload["synthesis"]["evidence_ids"] == ["e1"]


@pytest.mark.parametrize("evidence_ids", [("unknown",), ()])
def test_unknown_or_uncited_provider_output_falls_back(evidence_ids: tuple[str, ...]) -> None:
    provider = DeterministicMockProvider(text="Untrusted claim", evidence_ids=evidence_ids)
    answer = run_synthesis("when?", [evidence()], policy(), provider)
    assert answer.mode == "extractive"
    assert answer.provider_status == "invalid_response"
    assert answer.citations == ("tg://chat/10/message/7",)


def test_provider_failure_returns_local_evidence_and_sanitized_audit() -> None:
    class FailingProvider:
        def answer(self, _request):
            raise ProviderFailure("Timeout: api_key=super-secret")

    audits = []
    answer = run_synthesis("when?", [evidence()], policy(), FailingProvider(), audit_sink=audits.append)
    assert answer.provider_status == "provider_unavailable"
    audit = audits[0].as_dict()
    assert audit["failure_class"] == "timeout"
    assert "super-secret" not in json.dumps(audit)
    assert "The deadline" not in json.dumps(audit)


@pytest.mark.parametrize("exc,expected", [(OSError("connection failed"), "network"), (RuntimeError("private request content"), "provider_failure")])
def test_ordinary_adapter_exceptions_return_local_fallback(exc: Exception, expected: str) -> None:
    class FailingProvider:
        def answer(self, _request):
            raise exc

    audits = []
    answer = run_synthesis("when?", [evidence()], policy(), FailingProvider(), audit_sink=audits.append)
    assert answer.mode == "extractive"
    assert answer.provider_status == "provider_unavailable"
    assert audits[0].failure_class == expected
    assert "private request content" not in json.dumps(audits[0].as_dict())


def test_request_has_no_tools_or_archive_capabilities() -> None:
    provider = DeterministicMockProvider()
    run_synthesis("when?", [evidence()], policy(), provider)
    request = provider.calls[0]
    assert set(request.__dict__) == {"provider", "model", "question", "evidence", "token_budget"}
    assert not any("path" in key or "credential" in key or "tool" in key for key in request.__dict__)
    assert set(json.loads(serialize_provider_request(request))) == {"question", "evidence", "answer_contract"}


def test_budget_truncates_evidence_deterministically() -> None:
    small = policy(token_budget=300)
    safe = bound_evidence([evidence(text="x" * 200), evidence(message_id=8, text="short")], small)
    assert [item.citation for item in safe] == ["tg://chat/10/message/8"]


def test_final_serialized_request_budget_includes_question_and_denies_before_provider() -> None:
    provider = DeterministicMockProvider()
    audits = []
    question = "sensitive question " * 1_000

    with pytest.raises(SynthesisPolicyDenied, match="request exceeds"):
        run_synthesis(question, [evidence()], policy(token_budget=100), provider, audit_sink=audits.append)

    assert provider.calls == []
    assert audits[0].provider_status == "policy_denied"
    assert audits[0].evidence_count == 0
    assert question not in json.dumps(audits[0].as_dict())


def test_serialized_provider_request_never_exceeds_policy_budget() -> None:
    provider = DeterministicMockProvider()
    run_synthesis("when?", [evidence()], policy(token_budget=1_000), provider)
    assert len(serialize_provider_request(provider.calls[0]).encode("utf-8")) <= 1_000


def test_duplicate_citations_are_removed_before_context_budget_is_spent() -> None:
    safe = bound_evidence([evidence(text="first"), evidence(text="duplicate"), evidence(message_id=8, text="second")], policy())
    assert [(item.evidence_id, item.citation, item.text) for item in safe] == [
        ("e1", "tg://chat/10/message/7", "first"),
        ("e2", "tg://chat/10/message/8", "second"),
    ]


def test_usage_is_additive_contract_metadata() -> None:
    class UsageProvider:
        def answer(self, _request):
            return ProviderResponse("Friday", ("e1",), ProviderUsage(input_tokens=7, output_tokens=2))

    answer = run_synthesis("when?", [evidence()], policy(), UsageProvider())
    assert answer.as_dict()["synthesis"]["usage"] == {"input_tokens": 7, "output_tokens": 2}


def configured_archive(tmp_path) -> tuple[AppConfig, Database]:
    home = tmp_path / "home"
    cfg = AppConfig.default(home, "work")
    cfg.llm = LLMConfig(provider="openai-responses", model="gpt-test", api_key="sk-private")
    cfg.provider_policy = ProviderPolicy(
        external_llm_enabled=True,
        external_llm_data_classes=["message_text", "metadata"],
    )
    cfg.ai_access = AIAccessPolicy(enabled=True, allowed_chat_ids=[10], max_results=3)
    save_config(cfg, home=home)
    db = Database(cfg.db_path)
    db.migrate()
    db.upsert_chat(ChatRecord(10, "Work", "group"))
    db.upsert_message(MessageRecord(10, 7, datetime(2026, 1, 2, tzinfo=UTC), "Deadline is Friday."))
    return cfg, db


def test_external_llm_data_classes_config_roundtrip_is_typed_and_exact(tmp_path) -> None:
    cfg = AppConfig.default(tmp_path / "home", "work")
    configured = set_config_value(cfg, "provider_policy.external_llm_data_classes", "metadata,message_text")
    assert configured.provider_policy.external_llm_data_classes == ["message_text", "metadata"]
    save_config(configured, home=tmp_path / "home")
    loaded = load_config(home=tmp_path / "home", profile="work")
    assert loaded.provider_policy.external_llm_data_classes == ["message_text", "metadata"]
    with pytest.raises(ValueError, match="exactly"):
        set_config_value(loaded, "provider_policy.external_llm_data_classes", "message_text")


def test_configured_assistant_returns_additive_synthesis_and_sanitized_audit(tmp_path) -> None:
    cfg, db = configured_archive(tmp_path)
    provider = DeterministicMockProvider(text="Friday.")
    answer = ArchiveAssistant(db, cfg).answer_with_synthesis(
        "Friday", limit=2, filters=SearchFilters(chat_id=10), provider=provider, token_budget=1_000,
    )

    payload = answer.as_dict()
    assert payload["answer"] == "Friday."
    assert payload["citations"] == ["tg://chat/10/message/7"]
    assert payload["synthesis"]["mode"] == "synthesized"
    assert payload["synthesis"]["provider_status"] == "synthesized"
    assert payload["synthesis"]["provider"] == "openai-responses"
    assert payload["synthesis"]["model"] == "gpt-test"
    assert payload["synthesis"]["evidence_ids"] == ["e1"]
    assert payload["synthesis"]["failure_class"] is None
    assert payload["synthesis"]["usage"] is not None
    with db.connect() as conn:
        audit = conn.execute("SELECT details_json FROM audit_events WHERE event_type = 'llm_answer'").fetchone()["details_json"]
    assert "Deadline is Friday" not in audit and "sk-private" not in audit


def test_cli_extractive_json_is_unchanged_and_provider_denial_never_constructs_adapter(tmp_path, monkeypatch, capsys) -> None:
    cfg, _ = configured_archive(tmp_path)
    cfg.llm = LLMConfig()
    save_config(cfg, home=tmp_path / "home")
    assert main(["--home", str(tmp_path / "home"), "--profile", "work", "--json", "ask", "deadline", "--chat-id", "10"]) == 0
    assert set(json.loads(capsys.readouterr().out)) == {"answer"}

    cfg.llm = LLMConfig(provider="openai-responses", model="gpt-test", api_key="sk-private")
    cfg.provider_policy.external_llm_enabled = False
    save_config(cfg, home=tmp_path / "home")
    monkeypatch.setattr("tg_recall.assistant.OpenAIResponsesProvider", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not construct")))
    assert main(["--home", str(tmp_path / "home"), "--profile", "work", "--json", "ask", "deadline", "--chat-id", "10"]) == 0
    denied = json.loads(capsys.readouterr().out)
    assert denied["synthesis"]["provider_status"] == "policy_denied"
    assert denied["citations"] == ["tg://chat/10/message/7"]


def test_oversized_serialized_question_never_constructs_provider_and_falls_back(tmp_path, monkeypatch) -> None:
    cfg, db = configured_archive(tmp_path)
    constructed = []
    monkeypatch.setattr("tg_recall.assistant.OpenAIResponsesProvider", lambda **_kwargs: constructed.append(True))
    answer = ArchiveAssistant(db, cfg).answer_with_synthesis(
        "Friday OR " + "x" * 10_000,
        limit=2,
        filters=SearchFilters(chat_id=10),
        token_budget=100,
    )
    assert constructed == []
    assert answer.mode == "extractive"
    assert answer.provider_status == "policy_denied"
    assert answer.failure_class == "budget_exceeded"
    assert answer.citations == ("tg://chat/10/message/7",)


def test_provider_constructor_failure_is_a_stable_cited_fallback(tmp_path, monkeypatch) -> None:
    cfg, db = configured_archive(tmp_path)
    monkeypatch.setattr(
        "tg_recall.assistant.OpenAIResponsesProvider",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("private constructor failure")),
    )
    answer = ArchiveAssistant(db, cfg).answer_with_synthesis(
        "Friday", limit=2, filters=SearchFilters(chat_id=10), token_budget=1_000,
    )
    assert answer.mode == "extractive"
    assert answer.provider_status == "provider_unavailable"
    assert answer.failure_class == "configuration"
    assert answer.citations == ("tg://chat/10/message/7",)


def test_synthesis_uses_exact_human_chat_date_and_media_intersection_before_retrieval(tmp_path) -> None:
    cfg, db = configured_archive(tmp_path)
    db.upsert_message(MessageRecord(10, 8, datetime(2025, 12, 31, tzinfo=UTC), "old deadline document", has_media=True, media_type="document"))
    db.upsert_message(MessageRecord(10, 9, datetime(2026, 1, 4, tzinfo=UTC), "deadline voice", has_media=True, media_type="voice"))
    cfg.ai_access.allowed_since = "2026-01-01"
    cfg.ai_access.allowed_media_types = "voice"
    provider = DeterministicMockProvider()
    answer = ArchiveAssistant(db, cfg).answer_with_synthesis(
        "deadline", limit=5, filters=SearchFilters(chat_id=10), provider=provider, token_budget=2_000,
    )
    assert answer.mode == "synthesized"
    assert [item.citation for item in provider.calls[0].evidence] == ["tg://chat/10/message/9"]
    with db.connect() as conn:
        audit = json.loads(conn.execute("SELECT details_json FROM audit_events WHERE event_type = 'llm_answer'").fetchone()["details_json"])
    assert audit["effective_chat_ids"] == [10]
    assert audit["effective_since"] == "2026-01-01"
    assert audit["effective_media_policy"] == "voice"

    cfg.ai_access.allowed_media_types = "all"
    narrower = DeterministicMockProvider()
    ArchiveAssistant(db, cfg).answer_with_synthesis(
        "deadline",
        limit=5,
        filters=SearchFilters(chat_id=10, since="2026-01-04", media_type="voice"),
        provider=narrower,
        token_budget=2_000,
    )
    assert [item.citation for item in narrower.calls[0].evidence] == ["tg://chat/10/message/9"]


def test_synthesis_denies_out_of_chat_before_any_archive_read_or_provider(tmp_path, monkeypatch) -> None:
    cfg, db = configured_archive(tmp_path)
    db.upsert_chat(ChatRecord(11, "Hidden", "group"))
    db.upsert_message(MessageRecord(11, 1, datetime(2026, 1, 2, tzinfo=UTC), "hidden deadline"))
    provider = DeterministicMockProvider()
    monkeypatch.setattr(db, "search", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not search")))
    answer = ArchiveAssistant(db, cfg).answer_with_synthesis(
        "deadline", limit=2, filters=SearchFilters(chat_id=11), provider=provider, token_budget=1_000,
    )
    assert provider.calls == []
    assert answer.provider_status == "policy_denied"
    assert answer.citations == ()


def test_automation_synthesis_keeps_the_same_effective_scope(tmp_path, monkeypatch, capsys) -> None:
    cfg, db = configured_archive(tmp_path)
    db.upsert_message(MessageRecord(10, 8, datetime(2025, 12, 31, tzinfo=UTC), "old deadline document", has_media=True, media_type="document"))
    db.upsert_message(MessageRecord(10, 9, datetime(2026, 1, 4, tzinfo=UTC), "deadline voice", has_media=True, media_type="voice"))
    cfg.ai_access.allowed_since = "2026-01-01"
    cfg.ai_access.allowed_media_types = "voice"
    save_config(cfg, home=tmp_path / "home")
    provider = DeterministicMockProvider()
    monkeypatch.setattr("tg_recall.assistant.OpenAIResponsesProvider", lambda **_kwargs: provider)
    monkeypatch.setenv("TG_RECALL_AI_MODE", "1")
    assert main([
        "--home", str(tmp_path / "home"), "--profile", "work", "--json", "ask", "deadline", "--chat-id", "10",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["citations"] == ["tg://chat/10/message/9"]
    assert [item.citation for item in provider.calls[0].evidence] == ["tg://chat/10/message/9"]


def test_mcp_ask_is_read_only_extractive_even_with_an_external_provider_configured(tmp_path, monkeypatch) -> None:
    cfg, db = configured_archive(tmp_path)
    monkeypatch.setattr("tg_recall.assistant.OpenAIResponsesProvider", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("MCP must not construct provider")))
    response = ReadOnlyMCPServer(cfg, db).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "ask_archive", "arguments": {"query": "deadline", "chat_id": 10},
    }})
    assert "tg://chat/10/message/7" in response["result"]["content"][0]["text"]
