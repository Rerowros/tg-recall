from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from tg_recall.assistant import ArchiveAssistant
from tg_recall.config import AppConfig
from tg_recall.models import ChatRecord, MessageRecord, SearchFilters
from tg_recall.storage import Database


def load_fixture(db: Database) -> int:
    fixture = json.loads(Path("tests/fixtures/archive_fixture.json").read_text(encoding="utf-8"))
    db.migrate()
    for chat in fixture["chats"]:
        db.upsert_chat(ChatRecord(**chat))
    media_id = 0
    for item in fixture["messages"]:
        db.upsert_message(
            MessageRecord(
                chat_id=item["chat_id"],
                message_id=item["message_id"],
                date=datetime.fromisoformat(item["date"]),
                text=item["text"],
                sender_id=item.get("sender_id"),
                sender_name=item.get("sender_name"),
                has_media=item.get("has_media", False),
                media_type=item.get("media_type"),
                links_json=json.dumps(["https://example.com"]) if "https://example.com" in item["text"] else "[]",
            )
        )
        if item.get("media_type"):
            media_id = db.enqueue_media(item["chat_id"], item["message_id"], item["media_type"], "fixture")
    db.insert_transcript(media_id, "fixture", fixture["transcript"])
    return media_id


def test_end_to_end_fixture_search_and_context(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    load_fixture(db)

    results = db.search("deadline", filters=SearchFilters(chat_id=10, has_link=True))
    context = ArchiveAssistant(db, AppConfig.default()).retrieve("budget deadline", chat_id=10)
    answer = ArchiveAssistant(db, AppConfig.default()).answer("budget deadline", chat_id=10)

    assert results[0].message_id == 100
    assert "tg://chat/10/message/" in context.as_prompt_context()
    assert "Local extractive answer" in answer


def test_metadata_and_semantic_search(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    load_fixture(db)

    by_sender = db.search("deadline", filters=SearchFilters(chat_id=10, sender_id=7))
    semantic = db.semantic_search("approval budget", filters=SearchFilters(chat_id=10))

    assert by_sender[0].message_id == 100
    assert semantic[0].message_id == 101
