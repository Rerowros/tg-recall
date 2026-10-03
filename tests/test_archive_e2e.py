from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from tg_recall.agent_tools import AgentTools
from tg_recall.config import AppConfig
from tg_recall.models import ChatRecord, MessageRecord
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


def owner(db: Database) -> AgentTools:
    return AgentTools(AppConfig.default(), db, owner=True)


def test_owner_search_finds_messages_and_transcripts_with_citations(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    load_fixture(db)

    text = owner(db).search({"query": "deadline"}).text
    voice = owner(db).search({"query": "budget approval"}).text

    assert ">100 " in text and "tg://chat/10/message/100" in text
    assert ">101 " in voice and "«The budget approval deadline is next Monday»" in voice


def test_owner_filters_by_sender_and_reads_a_period(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    load_fixture(db)

    by_sender = owner(db).search({"query": "deadline", "from": "Ada"}).text
    period = owner(db).read({"chats": 10, "since": "2025-12-31", "until": "2026-01-03"}).text

    assert ">100 " in by_sender and ">101 " not in by_sender
    assert period.index("deadline moved") < period.index("Voice note")
