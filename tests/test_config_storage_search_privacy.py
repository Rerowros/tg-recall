from __future__ import annotations

from datetime import UTC, datetime

from tg_recall.config import AppConfig, TelegramConfig, redact_config, set_config_value
from tg_recall.models import ChatRecord, MessageRecord
from tg_recall.storage import Database


def test_config_redacts_secrets() -> None:
    cfg = AppConfig(telegram=TelegramConfig(api_id=1, api_hash="secret", phone="+100", session_path="session"))

    redacted = redact_config(cfg)

    assert redacted["telegram"]["api_hash"] == "***REDACTED***"
    assert redacted["telegram"]["phone"] == "***REDACTED***"
    assert redacted["telegram"]["session_path"] == "***REDACTED***"


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
