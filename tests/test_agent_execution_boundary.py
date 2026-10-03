from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tg_recall.cli import main
from tg_recall.config import AIAccessPolicy, AppConfig, TelegramConfig, save_config
from tg_recall.mcp_server import ReadOnlyMCPServer
from tg_recall.models import ChatRecord, MessageRecord
from tg_recall.security import AgentOperation, AgentPolicyError, RequestedAgentScope, require_agent_policy
from tg_recall.storage import Database
from tg_recall.telegram_client import TelegramArchiveClient


def agent_archive(tmp_path) -> tuple[AppConfig, Database]:
    cfg = AppConfig.default(tmp_path / "home")
    cfg.ai_access = AIAccessPolicy(
        enabled=True,
        allowed_chat_ids=[10],
        max_results=1,
        allowed_since="2026-01-01",
        allowed_media_types="all",
    )
    save_config(cfg, home=tmp_path / "home")
    db = Database(cfg.db_path)
    db.migrate()
    db.upsert_chat(ChatRecord(chat_id=10, title="Allowed", chat_type="group"))
    db.upsert_chat(ChatRecord(chat_id=11, title="Hidden", chat_type="group"))
    db.upsert_message(MessageRecord(chat_id=10, message_id=1, date=datetime(2026, 1, 2, tzinfo=UTC), text="deadline"))
    db.upsert_message(MessageRecord(chat_id=10, message_id=2, date=datetime(2025, 12, 2, tzinfo=UTC), text="old deadline"))
    return cfg, db


def test_policy_intersects_saved_scope_dates_media_and_limit() -> None:
    decision = require_agent_policy(
        AgentOperation.ARCHIVE_READ,
        enabled=True,
        allowed_chat_ids=[10],
        max_results=5,
        allowed_since="2026-01-10",
        allowed_until="2026-02-10",
        allowed_media_types="voice,photo",
        automation=True,
        requested=RequestedAgentScope(
            chat_ids=(10,),
            since="2026-01-01",
            until="2026-03-01",
            media_policy="all",
            result_limit=20,
            saved_scope={"chat_ids": [10], "media_policy": "voice,video"},
        ),
    )

    assert decision.chat_ids == (10,)
    assert decision.since == "2026-01-10"
    assert decision.until == "2026-02-10"
    assert decision.media_policy == "voice"
    assert decision.result_limit == 5


def test_policy_intersects_requested_and_saved_chat_scopes() -> None:
    decision = require_agent_policy(
        AgentOperation.SYNC,
        enabled=True,
        allowed_chat_ids=[10, 11],
        max_results=5,
        automation=True,
        requested=RequestedAgentScope(chat_ids=(11,), saved_scope={"chat_ids": [10, 11]}),
    )
    assert decision.chat_ids == (11,)

    with pytest.raises(AgentPolicyError) as exc_info:
        require_agent_policy(
            AgentOperation.SYNC,
            enabled=True,
            allowed_chat_ids=[10, 11],
            max_results=5,
            automation=True,
            requested=RequestedAgentScope(chat_ids=(11,), saved_scope={"chat_ids": [10]}),
        )
    assert exc_info.value.error_code == "scope_empty"


def test_policy_rejects_invalid_dates_and_restrictive_result_config() -> None:
    with pytest.raises(AgentPolicyError) as date_error:
        require_agent_policy(
            AgentOperation.SYNC,
            enabled=True,
            allowed_chat_ids=[10],
            max_results=5,
            allowed_since="2026-01-01junk",
            automation=True,
            requested=RequestedAgentScope(chat_ids=(10,)),
        )
    assert date_error.value.error_code == "invalid_scope_date"
    assert date_error.value.decision.operation == AgentOperation.SYNC

    for requested in (
        RequestedAgentScope(chat_ids=(10,), since="2026-01-01junk"),
        RequestedAgentScope(chat_ids=(10,), saved_scope={"chat_ids": [10], "until": "bad-date"}),
    ):
        with pytest.raises(AgentPolicyError) as invalid_scope:
            require_agent_policy(
                AgentOperation.SYNC,
                enabled=True,
                allowed_chat_ids=[10],
                max_results=5,
                automation=True,
                requested=requested,
            )
        assert invalid_scope.value.error_code == "invalid_scope_date"
        assert invalid_scope.value.decision.operation == AgentOperation.SYNC

    with pytest.raises(AgentPolicyError) as limit_error:
        require_agent_policy(
            AgentOperation.ARCHIVE_READ,
            enabled=True,
            allowed_chat_ids=[10],
            max_results=0,
            automation=True,
            requested=RequestedAgentScope(chat_ids=(10,), result_limit=20),
        )
    assert limit_error.value.error_code == "invalid_ai_access_limit"


def test_agent_cli_caps_scoped_read_and_returns_stable_denial(tmp_path, monkeypatch, capsys) -> None:
    cfg, _ = agent_archive(tmp_path)
    monkeypatch.setenv("TG_RECALL_AI_MODE", "1")

    assert main(["--home", str(tmp_path / "home"), "--json", "search", "deadline", "--chat-id", "10", "--limit", "20"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    assert rows[0]["message_id"] == 1

    assert main(["--home", str(tmp_path / "home"), "--json", "config", "set", "llm.provider", "extractive"]) == 1
    denied = json.loads(capsys.readouterr().out)
    assert denied["error"]["code"] == "agent_operation_forbidden"

    with cfg_path_audit(cfg) as rows:
        assert any(row["event_type"] == "agent_policy_decision" for row in rows)


def test_agent_export_cannot_escape_private_exports_directory(tmp_path, monkeypatch, capsys) -> None:
    _, _ = agent_archive(tmp_path)
    monkeypatch.setenv("TG_RECALL_AI_MODE", "1")

    assert main(
        [
            "--home", str(tmp_path / "home"), "--json", "export", "--chat", "10",
            "--output", str(tmp_path / "outside.jsonl"),
        ]
    ) == 1
    denied = json.loads(capsys.readouterr().out)
    assert denied["error"]["code"] == "private_export_path_required"
    assert not (tmp_path / "outside.jsonl").exists()


def test_mcp_metadata_requires_enabled_access(tmp_path) -> None:
    cfg, db = agent_archive(tmp_path)
    cfg.ai_access.enabled = False
    response = ReadOnlyMCPServer(cfg, db).handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "chats", "arguments": {}}}
    )

    assert response["result"]["isError"] is True
    assert "ai_access_disabled" in response["result"]["content"][0]["text"]


def test_multi_media_policy_filters_cli_mcp_and_date_bounded_context(tmp_path, monkeypatch, capsys) -> None:
    cfg, db = agent_archive(tmp_path)
    cfg.ai_access.max_results = 10
    cfg.ai_access.allowed_media_types = "voice,photo"
    save_config(cfg, home=tmp_path / "home")
    db.upsert_message(
        MessageRecord(
            chat_id=10,
            message_id=3,
            date=datetime(2026, 1, 3, tzinfo=UTC),
            text="deadline voice",
            has_media=True,
            media_type="voice",
        )
    )
    db.upsert_message(
        MessageRecord(
            chat_id=10,
            message_id=4,
            date=datetime(2026, 1, 4, tzinfo=UTC),
            text="deadline document",
            has_media=True,
            media_type="document",
        )
    )
    monkeypatch.setenv("TG_RECALL_AI_MODE", "1")
    assert main(["--home", str(tmp_path / "home"), "--json", "search", "deadline", "--chat-id", "10"]) == 0
    cli_rows = json.loads(capsys.readouterr().out)
    assert [row["message_id"] for row in cli_rows] == [3]

    assert main(["--home", str(tmp_path / "home"), "--json", "search", "deadline", "--chat-id", "10", "--semantic"]) == 0
    semantic_rows = json.loads(capsys.readouterr().out)
    assert [row["message_id"] for row in semantic_rows] == [3]

    mcp_rows = ReadOnlyMCPServer(cfg, db).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search", "arguments": {"query": "deadline", "chat_id": 10, "context": 0}},
        }
    )
    cited = mcp_rows["result"]["content"][0]["text"].split("cite: ", 1)[1].split()
    # The MCP media policy hides disallowed media but keeps plain text; the date bound still applies.
    assert sorted(cited) == ["tg://chat/10/message/1", "tg://chat/10/message/3"]

    cfg.ai_access.allowed_media_types = "all"
    cfg.ai_access.allowed_since = "2026-01-01"
    context = ReadOnlyMCPServer(cfg, db).handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "read", "arguments": {"refs": ["10/3"], "before": 2, "after": 0}},
        }
    )
    window = context["result"]["content"][0]["text"]
    assert ">3 " in window and "\n 1 " in window
    assert "old deadline" not in window


def test_successful_noop_sync_preserves_watermarks_and_clears_expired_retry(tmp_path, monkeypatch) -> None:
    cfg = AppConfig(telegram=TelegramConfig(api_id=1, api_hash="hash"))
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    db.create_scope("work", [10], None, None)
    db.update_sync_state(10, newest_message_id=50, oldest_message_id=10, retry_after="2026-01-02T00:00:00+00:00")
    client = TelegramArchiveClient(cfg, db)

    class EmptyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def iter_messages(self, chat_id, limit=100, **kwargs):
            if False:
                yield None

    monkeypatch.setattr(client, "_client", lambda: EmptyClient())
    asyncio.run(client.sync_scope("work"))

    state = db.get_sync_state(10)
    assert state is not None
    assert state["newest_message_id"] == 50
    assert state["oldest_message_id"] == 10
    assert state["retry_after"] is None


def test_bounded_and_interrupted_sync_do_not_move_watermarks_the_wrong_way(tmp_path, monkeypatch) -> None:
    cfg = AppConfig(telegram=TelegramConfig(api_id=1, api_hash="hash"))
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    db.create_scope("bounded", [10], "2026-02-01", None)
    db.update_sync_state(10, newest_message_id=50, oldest_message_id=10)
    client = TelegramArchiveClient(cfg, db)

    class BoundedClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def iter_messages(self, chat_id, limit=100, **kwargs):
            yield _telegram_message(60, datetime(2026, 1, 1, tzinfo=UTC))

    monkeypatch.setattr(client, "_client", lambda: BoundedClient())
    asyncio.run(client.sync_scope("bounded"))
    assert db.get_sync_state(10)["newest_message_id"] == 50
    assert db.get_sync_state(10)["oldest_message_id"] == 10

    class InterruptedClient(BoundedClient):
        async def iter_messages(self, chat_id, limit=100, **kwargs):
            yield _telegram_message(70, datetime(2026, 2, 2, tzinfo=UTC))
            raise RuntimeError("connection interrupted")

    monkeypatch.setattr(client, "_client", lambda: InterruptedClient())
    with pytest.raises(RuntimeError, match="interrupted"):
        asyncio.run(client.sync_scope("bounded"))
    assert db.get_sync_state(10)["newest_message_id"] == 50
    assert db.get_sync_state(10)["oldest_message_id"] == 10


def _telegram_message(message_id: int, value: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        date=value,
        raw_text="deadline",
        text=None,
        sender_id=7,
        sender=None,
        reply_to=None,
        fwd_from=None,
        edit_date=None,
        voice=None,
        audio=None,
        video=None,
        photo=None,
        document=None,
        media=None,
    )


class cfg_path_audit:
    def __init__(self, cfg: AppConfig):
        self.db = Database(cfg.db_path)

    def __enter__(self):
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT event_type FROM audit_events")]

    def __exit__(self, exc_type, exc, tb):
        return False


def test_claude_code_shell_cannot_read_or_reconfigure_outside_allowlist(tmp_path, monkeypatch, capsys) -> None:
    agent_archive(tmp_path)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setenv("CLAUDECODE", "1")
    home = ["--home", str(tmp_path / "home"), "--json"]

    assert main([*home, "search", "deadline", "--chat-id", "11"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "chat_not_allowed"

    assert main([*home, "config", "set", "ai_access.allowed_chat_ids", "10,11"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "agent_operation_forbidden"
