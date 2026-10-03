from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

import tg_recall.cli as cli
from tg_recall.assistant import ArchiveAssistant
from tg_recall.cli import main
from tg_recall.config import AppConfig, save_config
from tg_recall.context_budgeting import CharClassTokenCounter, canonical_json_bytes
from tg_recall.hybrid_retrieval import EmbeddingModelMetadata, RetrievalMode, SemanticUnavailableError
from tg_recall.mcp_server import ReadOnlyMCPServer
from tg_recall.models import ChatRecord, MessageRecord, SearchFilters
from tg_recall.storage import Database


class FixtureProvider:
    metadata = EmbeddingModelMetadata("fixture", "integration", 2, "b" * 64)

    def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        return [(1.0, 0.0) if "alpha" in text else (0.0, 1.0) for text in texts]


def archive(tmp_path) -> tuple[AppConfig, Database]:
    cfg = AppConfig.default(tmp_path / "home", "work")
    save_config(cfg, home=tmp_path / "home")
    db = Database(cfg.db_path)
    db.migrate()
    db.upsert_chat(ChatRecord(10, "Allowed", "group"))
    db.upsert_chat(ChatRecord(20, "Other", "group"))
    db.upsert_message(MessageRecord(10, 1, datetime(2026, 1, 2, tzinfo=UTC), "alpha deadline"))
    db.upsert_message(MessageRecord(20, 1, datetime(2026, 1, 2, tzinfo=UTC), "alpha hidden"))
    media_id = db.enqueue_media(10, 1, "voice", "fixture")
    db.insert_transcript(media_id, "fixture", "alpha deadline spoken")
    return cfg, db


def test_hybrid_candidates_share_filters_dedupe_and_budgeted_windows(tmp_path) -> None:
    cfg, db = archive(tmp_path)
    provider = FixtureProvider()
    db.build_embedding_index(provider, batch_size=8)

    result = ArchiveAssistant(db, cfg, embedding_provider=provider).retrieve_hybrid(
        "alpha",
        filters=SearchFilters(chat_id=10, since="2026-01-01", until="2026-01-03"),
        limit=2,
        token_budget=1000,
        context_radius=1,
        mode=RetrievalMode.AUTO,
    )

    assert result.mode == RetrievalMode.HYBRID
    assert result.candidate_limits == {"keyword": 6, "semantic": 6}
    assert [item.citation for item in result.evidence] == ["tg://chat/10/message/1"]
    assert result.evidence[0].provenance == ("keyword", "semantic")
    assert all(context.chat_id == 10 for context in result.evidence[0].context)
    assert "alpha hidden" not in json.dumps(result.as_json())


def test_auto_fallback_and_strict_semantic_are_distinct(tmp_path) -> None:
    cfg, db = archive(tmp_path)
    assistant = ArchiveAssistant(db, cfg)
    filters = SearchFilters(chat_id=10)

    automatic = assistant.retrieve_hybrid("alpha", filters=filters, limit=1, token_budget=100, mode=RetrievalMode.AUTO)
    assert automatic.mode == RetrievalMode.KEYWORD
    assert automatic.fallback_reason == "embedding_provider_unavailable"
    with pytest.raises(SemanticUnavailableError) as error:
        assistant.retrieve_hybrid("alpha", filters=filters, limit=1, token_budget=100, mode=RetrievalMode.SEMANTIC)
    assert error.value.reason == "embedding_provider_unavailable"


def test_hybrid_payload_budget_counts_exact_utf8_json_with_multibyte_context(tmp_path) -> None:
    cfg, db = archive(tmp_path)
    db.upsert_message(
        MessageRecord(
            10,
            1,
            datetime(2026, 1, 2, tzinfo=UTC),
            "🚀 Привет, это очень длинный контекст с JSON-границами. " * 80 + "дедлайн",
        )
    )
    result = ArchiveAssistant(db, cfg).retrieve_hybrid(
        "дедлайн", filters=SearchFilters(chat_id=10), limit=1, token_budget=400, mode=RetrievalMode.AUTO
    )
    payload = result.as_json()
    exact = CharClassTokenCounter().count(canonical_json_bytes({"evidence": payload["evidence"]}))

    assert payload["accounting"]["counter"] == "charclass"
    assert payload["accounting"]["estimated_tokens"] == exact
    assert exact <= payload["accounting"]["usable_payload_budget"]
    assert payload["truncated"] is True
    assert payload["evidence"][0]["citation"] == "tg://chat/10/message/1"


def test_cli_and_read_only_mcp_expose_machine_retrieval_provenance(tmp_path, capsys) -> None:
    cfg, db = archive(tmp_path)
    assert main([
        "--home", str(tmp_path / "home"), "--profile", "work", "--json", "retrieve", "alpha",
        "--chat-id", "10", "--retrieval-mode", "auto", "--token-budget", "1000",
    ]) == 0
    cli_payload = json.loads(capsys.readouterr().out)
    assert cli_payload["mode"] == "keyword"
    assert cli_payload["fallback_reason"] == "embedding_provider_unavailable"
    assert cli_payload["evidence"][0]["citation"] == "tg://chat/10/message/1"

    cfg.ai_access.enabled = True
    cfg.ai_access.allowed_chat_ids = [10]
    cfg.ai_access.max_results = 2
    server = ReadOnlyMCPServer(cfg, db)
    assert "retrieve_evidence" in {tool["name"] for tool in server.tools()}
    response = server.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "retrieve_evidence", "arguments": {"query": "alpha", "chat_id": 10, "mode": "auto", "token_budget": 1000}},
    })
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["mode"] == "keyword"
    assert payload["evidence"][0]["citation"] == "tg://chat/10/message/1"
    denied = server.handle({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "index_embeddings_build", "arguments": {"chat_id": 10}},
    })
    assert denied["error"]["code"] == -32000


def test_cli_strict_semantic_has_stable_unavailable_json(tmp_path, capsys) -> None:
    _, _ = archive(tmp_path)
    assert main([
        "--home", str(tmp_path / "home"), "--profile", "work", "--json", "search", "alpha",
        "--chat-id", "10", "--retrieval-mode", "semantic",
    ]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == {
        "code": "semantic_unavailable",
        "message": "semantic_unavailable: embedding_provider_unavailable",
        "reason": "embedding_provider_unavailable",
    }


def test_cli_embedding_build_resumes_status_and_explicit_removal(tmp_path, monkeypatch, capsys) -> None:
    _, _ = archive(tmp_path)
    provider = FixtureProvider()
    monkeypatch.setattr(cli, "local_embedding_provider", lambda _cfg: provider)
    root_args = ["--home", str(tmp_path / "home"), "--profile", "work", "--json", "index", "embeddings"]

    assert main([*root_args, "build", "--chat-id", "10", "--max-batches", "1", "--batch-size", "1"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["batches_committed"] == 1
    assert first["remaining_records"] == 1
    assert provider.embed.__self__ is provider

    assert main([*root_args, "build", "--chat-id", "10", "--max-batches", "2", "--batch-size", "1"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["remaining_records"] == 0
    assert second["index"]["status"] == "current"

    assert main([*root_args, "status", "--chat-id", "10"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["provider_available"] is True
    assert status["index"]["status"] == "current"

    assert main([*root_args, "remove", "--model-identity", provider.metadata.identity]) == 0
    assert json.loads(capsys.readouterr().out) == {"scope": "model", "models_deleted": 1}
