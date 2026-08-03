from __future__ import annotations

import json
from datetime import UTC, datetime

from tg_recall.config import AppConfig, LLMConfig, TelegramConfig, load_config, redact_config, save_config, set_config_value
from tg_recall.paths import AppPaths
from tg_recall.models import ChatRecord, MessageRecord
from tg_recall.storage import Database


def test_config_redacts_secrets() -> None:
    cfg = AppConfig(
        telegram=TelegramConfig(api_id=1, api_hash="secret", phone="+100", session_path="session"),
        llm=LLMConfig(provider="openai-responses", model="gpt-test", api_key="provider-secret"),
    )

    redacted = redact_config(cfg)

    assert redacted["telegram"]["api_hash"] == "***REDACTED***"
    assert redacted["telegram"]["phone"] == "***REDACTED***"
    assert redacted["telegram"]["session_path"] == "***REDACTED***"
    assert redacted["llm"]["api_key"] == "***REDACTED***"
    assert "provider-secret" not in json.dumps(redacted)


def test_profile_llm_identity_and_private_credentials_are_split_without_changing_extractive_default(tmp_path) -> None:
    home = tmp_path / "portable"
    config = AppConfig.default(home, "work")
    assert config.llm == LLMConfig()

    config.llm = LLMConfig(provider="openai-responses", model="gpt-test", api_key="provider-secret")
    save_config(config, home=home)
    paths = AppPaths.resolve(home, "work")
    profile = json.loads(paths.profile_config_path.read_text(encoding="utf-8"))
    credentials = json.loads(paths.credentials_path.read_text(encoding="utf-8"))
    loaded = load_config(home=home, profile="work")

    assert profile["llm"] == {"provider": "openai-responses", "model": "gpt-test"}
    assert "provider-secret" not in paths.config_path.read_text(encoding="utf-8")
    assert "provider-secret" not in paths.profile_config_path.read_text(encoding="utf-8")
    assert credentials["llm"] == {"api_key": "provider-secret"}
    assert loaded.llm == config.llm


def test_explicit_legacy_config_path_keeps_its_monolithic_compatibility_contract(tmp_path) -> None:
    config = AppConfig.default(tmp_path / "home")
    config.llm = LLMConfig(provider="openai-responses", model="gpt-test", api_key="provider-secret")
    legacy_path = tmp_path / "legacy-config.json"

    save_config(config, path=legacy_path)
    loaded = load_config(legacy_path)

    assert json.loads(legacy_path.read_text(encoding="utf-8"))["llm"]["api_key"] == "provider-secret"
    assert loaded.llm == config.llm
    assert "provider-secret" not in json.dumps(redact_config(loaded))


def test_set_config_value_casts_api_id() -> None:
    cfg = set_config_value(AppConfig.default(), "telegram.api_id", "123")

    assert cfg.telegram.api_id == 123


def test_database_search_returns_citations(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    db.upsert_chat(ChatRecord(chat_id=10, title="Work", chat_type="group"))
    db.upsert_message(
        MessageRecord(
            chat_id=10,
            message_id=99,
            date=datetime(2026, 1, 1, tzinfo=UTC),
            text="Payment deadline is Friday",
        )
    )

    results = db.search("deadline")

    assert len(results) == 1
    assert results[0].citation == "tg://chat/10/message/99"
    assert results[0].chat_title == "Work"


def test_database_search_returns_transcripts(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    db.upsert_chat(ChatRecord(chat_id=10, title="Work", chat_type="group"))
    db.upsert_message(
        MessageRecord(
            chat_id=10,
            message_id=100,
            date=datetime(2026, 1, 1, tzinfo=UTC),
            text="Voice note attached",
            has_media=True,
            media_type="voice",
        )
    )
    media_id = db.enqueue_media(10, 100, "voice", "100")
    transcript_id = db.insert_transcript(media_id, "test", "The launch deadline moved to Monday")

    results = db.search("launch")

    assert len(results) == 1
    assert results[0].transcript_id == transcript_id
    assert results[0].media_id == media_id
    assert results[0].citation == "tg://chat/10/message/100"


def test_scope_requires_chat(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()

    try:
        db.create_scope("empty", [], None, None)
    except ValueError as exc:
        assert "at least one chat" in str(exc)
    else:
        raise AssertionError("Expected empty scope to fail")


def test_purge_chat_removes_indexed_data(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    db.upsert_chat(ChatRecord(chat_id=10, title="Work", chat_type="group"))
    db.upsert_message(
        MessageRecord(
            chat_id=10,
            message_id=99,
            date=datetime(2026, 1, 1, tzinfo=UTC),
            text="Sensitive deadline",
        )
    )

    result = db.purge_chat(10)

    assert result["messages"] == 1
    assert db.search("deadline") == []
