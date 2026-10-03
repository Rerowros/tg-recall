"""Archives made by old releases still work after an upgrade.

Each ``tests/fixtures/upgrade/<tag>.zip`` is an essential backup that the
release ``<tag>`` itself wrote for the same synthetic Acme archive
(``scripts/make_upgrade_fixture.py``). The current code must restore it,
migrate it in place and serve the same messages, citations, transcripts and
agent settings.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tg_recall.backup import create_backup, restore_backup
from tg_recall.config import load_config
from tg_recall.mcp_server import ReadOnlyMCPServer
from tg_recall.storage import SCHEMA_VERSION, Database

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "upgrade").glob("*.zip"))
GROUP, CHANNEL = -1001234567890, -1009876543210


def restored(tmp_path: Path, fixture: Path) -> tuple[ReadOnlyMCPServer, Database, Path]:
    home = tmp_path / "home"
    restore_backup(fixture, home=home, profile="default")
    config = load_config(home=home, profile="default")
    db = Database(config.db_path)
    db.migrate()
    return ReadOnlyMCPServer(config, db), db, home


def call(server: ReadOnlyMCPServer, name: str, arguments: dict) -> str:
    response = server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    )
    assert "result" in response, response
    assert not response["result"].get("isError"), response["result"]
    return response["result"]["content"][0]["text"]


def test_fixtures_cover_every_schema_generation() -> None:
    assert {path.stem for path in FIXTURES} >= {"v0.2.0", "v0.6.0", "v0.8.1"}


@pytest.mark.parametrize("fixture", FIXTURES, ids=[path.stem for path in FIXTURES])
def test_old_backup_restores_and_migrates_to_current_schema(tmp_path: Path, fixture: Path) -> None:
    server, db, _ = restored(tmp_path, fixture)
    db.migrate()  # a second start must be a no-op

    with sqlite3.connect(db.path) as conn:
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        messages = conn.execute("SELECT chat_id, message_id FROM messages ORDER BY chat_id, message_id").fetchall()
        transcripts = conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert versions[-1] == SCHEMA_VERSION
    assert messages == [(CHANNEL, 10), (CHANNEL, 11), *[(GROUP, n) for n in range(1, 7)]]
    assert transcripts == 1


@pytest.mark.parametrize("fixture", FIXTURES, ids=[path.stem for path in FIXTURES])
def test_old_archive_answers_agents_with_the_same_citations(tmp_path: Path, fixture: Path) -> None:
    server, _, _ = restored(tmp_path, fixture)
    config = server.config

    # Agent settings survive the upgrade.
    assert config.ai_access.enabled is True
    assert config.ai_access.allowed_chat_ids == [GROUP, CHANNEL]
    assert config.ai_access.max_results == 15

    found = call(server, "search", {"query": "payments", "chats": GROUP})
    assert f"tg://chat/{GROUP}/message/1" in found
    assert f"tg://chat/{GROUP}/message/3" in found

    voice = call(server, "search", {"query": "оплата"})
    assert f"tg://chat/{GROUP}/message/5" in voice  # found through its transcript
    assert f"tg://chat/{GROUP}/message/6" in voice

    read = call(server, "read", {"refs": [f"tg://chat/{GROUP}/message/3", f"{CHANNEL}/11"]})
    assert "Payments work again" in read and "Estonia" in read

    stats = call(server, "stats", {"chats": GROUP, "by": "month"})
    assert "2026-06" in stats and "2026-08" in stats

    chats = call(server, "chats", {})
    assert "AcmeChat" in chats and "Acme News" in chats


@pytest.mark.parametrize("fixture", FIXTURES, ids=[path.stem for path in FIXTURES])
def test_upgraded_archive_backs_up_and_restores_again(tmp_path: Path, fixture: Path) -> None:
    server, _, home = restored(tmp_path, fixture)
    backup = create_backup(server.config, tmp_path / "again.zip")

    restore_backup(backup["path"], home=home, profile="copy")
    copy = Database(load_config(home=home, profile="copy").db_path)
    copy.migrate()

    with sqlite3.connect(copy.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 8
