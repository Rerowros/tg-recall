from __future__ import annotations

import json
from datetime import UTC, datetime

from tg_recall.config import AppConfig, TelegramConfig, load_config, redact_config, save_config, set_config_value
from tg_recall.paths import AppPaths
from tg_recall.models import ChatRecord, MessageRecord, SearchFilters
from tg_recall.storage import Database


def test_set_config_value_casts_api_id() -> None:
    cfg = set_config_value(AppConfig.default(), "telegram.api_id", "123")

    assert cfg.telegram.api_id == 123


def test_config_redacts_secrets() -> None:
    cfg = AppConfig(telegram=TelegramConfig(api_id=1, api_hash="secret", phone="+100", session_path="session"))

    redacted = redact_config(cfg)

    assert redacted["telegram"]["api_hash"] == "***REDACTED***"
    assert redacted["telegram"]["phone"] == "***REDACTED***"
    assert redacted["telegram"]["session_path"] == "***REDACTED***"
    assert "secret" not in json.dumps(redacted)


def test_profile_keeps_credentials_out_of_profile_json_and_ignores_removed_keys(tmp_path) -> None:
    home = tmp_path / "portable"
    config = AppConfig.default(home, "work")
    config.telegram.api_hash = "provider-secret"
    save_config(config, home=home)
    paths = AppPaths.resolve(home, "work")
    profile = json.loads(paths.profile_config_path.read_text(encoding="utf-8"))
    profile["llm"] = {"provider": "openai-responses"}
    profile["semantic"] = {"enabled": True}
    profile["ai_access"]["mcp_research_tools"] = True
    paths.profile_config_path.write_text(json.dumps(profile), encoding="utf-8")

    loaded = load_config(home=home, profile="work")

    assert "provider-secret" not in paths.profile_config_path.read_text(encoding="utf-8")
    assert loaded.telegram.api_hash == "provider-secret"
    assert not hasattr(loaded, "llm") and not hasattr(loaded.ai_access, "mcp_research_tools")


def test_list_values_accept_commas_brackets_and_spaces() -> None:
    for value in ("-100,-200", "[-100, -200]", "-100 -200"):
        assert set_config_value(AppConfig.default(), "ai_access.allowed_chat_ids", value).ai_access.allowed_chat_ids == [-100, -200]


def test_purge_chat_removes_messages_and_index_rows(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    db.upsert_chat(ChatRecord(chat_id=10, title="Work", chat_type="group"))
    db.upsert_message(MessageRecord(chat_id=10, message_id=99, date=datetime(2026, 1, 1, tzinfo=UTC), text="Sensitive deadline"))

    result = db.purge_chat(10)

    assert result["messages"] == 1
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'deadline'").fetchone()[0] == 0


def test_export_filter_never_widens_beyond_the_chat_set(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    for chat_id in (10, 11, 12):
        db.upsert_chat(ChatRecord(chat_id=chat_id, title=f"chat {chat_id}", chat_type="group"))
        db.upsert_message(MessageRecord(chat_id=chat_id, message_id=1, date=datetime(2026, 1, 2, tzinfo=UTC), text="deadline"))

    assert {row["chat_id"] for row in db.export_messages(SearchFilters(chat_ids=(10, 11)))} == {10, 11}
    assert db.export_messages(SearchFilters(chat_ids=())) == []
    assert SearchFilters(chat_id=12, chat_ids=(10, 11)).normalized().chat_ids == ()


def test_migrate_drops_removed_feature_tables_but_keeps_authored_data(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    with db.connect() as conn:
        conn.execute("CREATE TABLE semantic_index (source_id INTEGER)")
        conn.execute("INSERT INTO semantic_index VALUES (1)")
        conn.execute("CREATE TABLE sync_scopes (name TEXT)")
        conn.execute("CREATE TABLE wiki_snapshots (snapshot_id TEXT)")
        conn.execute("INSERT INTO wiki_snapshots VALUES ('kept')")
        conn.execute("CREATE TABLE research_sessions (session_id TEXT)")

    db.migrate()

    with db.connect() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not {"semantic_index", "sync_scopes", "research_sessions", "embedding_vectors"} & tables
    assert "wiki_snapshots" in tables  # holds data, left alone
