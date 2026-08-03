from __future__ import annotations

from datetime import UTC, datetime

from tg_recall import __version__
from tg_recall.config import AIAccessPolicy, AppConfig
from tg_recall.mcp_server import ReadOnlyMCPServer
from tg_recall.models import ChatRecord, MessageRecord
from tg_recall.storage import Database


def server_with_data(tmp_path, allowed: list[int]) -> ReadOnlyMCPServer:
    cfg = AppConfig.default()
    cfg.ai_access = AIAccessPolicy(enabled=True, allowed_chat_ids=allowed, max_results=2)
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    db.upsert_chat(ChatRecord(chat_id=10, title="Work", chat_type="group"))
    db.upsert_message(MessageRecord(chat_id=10, message_id=1, date=datetime(2026, 1, 1, tzinfo=UTC), text="deadline"))
    return ReadOnlyMCPServer(cfg, db)


def test_mcp_initialize_reports_package_release_version(tmp_path) -> None:
    response = server_with_data(tmp_path, [10]).handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})

    assert __version__ == "0.5.0"
    assert response["result"]["serverInfo"] == {"name": "tg-recall", "version": __version__}


def test_mcp_search_is_read_only_and_scoped(tmp_path) -> None:
    server = server_with_data(tmp_path, [10])

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_messages", "arguments": {"query": "deadline", "chat_id": 10, "limit": 10}},
        }
    )

    assert "result" in response
    assert "tg://chat/10/message/1" in response["result"]["content"][0]["text"]


def test_mcp_denies_out_of_scope_chat(tmp_path) -> None:
    server = server_with_data(tmp_path, [10])

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_messages", "arguments": {"query": "deadline", "chat_id": 11}},
        }
    )

    assert "error" in response
    assert "not allowed" in response["error"]["message"]


def test_mcp_rejects_write_tool(tmp_path) -> None:
    server = server_with_data(tmp_path, [10])

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "purge", "arguments": {"chat_id": 10}},
        }
    )

    assert "error" in response
    assert "not available" in response["error"]["message"]
