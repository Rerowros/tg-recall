from __future__ import annotations

from datetime import UTC, datetime

from tg_ecosystem.media import MediaDownloader, MediaStore
from tg_ecosystem.models import ChatRecord, MessageRecord
from tg_ecosystem.storage import Database
from tg_ecosystem.transcription import TranscriptionService


def seed_voice(db: Database) -> int:
    db.migrate()
    db.upsert_chat(ChatRecord(chat_id=10, title="Work", chat_type="group"))
    db.upsert_message(
        MessageRecord(
            chat_id=10,
            message_id=5,
            date=datetime(2026, 1, 1, tzinfo=UTC),
            text="voice",
            has_media=True,
            media_type="voice",
        )
    )
    return db.enqueue_media(10, 5, "voice", "voice-5")


def test_media_download_failure_becomes_retryable_job(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    seed_voice(db)

    def fail_download(chat_id, message_id, destination):
        raise RuntimeError("rate limited")

    result = MediaDownloader(db, MediaStore(tmp_path / "media"), fail_download).run_pending()

    assert result["failed"] == 1
    with db.connect() as conn:
        row = conn.execute("SELECT status, retryable, error FROM jobs WHERE stage = 'media_download' ORDER BY id LIMIT 1").fetchone()
    assert row["status"] == "failed"
    assert row["retryable"] == 1
    assert "rate limited" in row["error"]


def test_transcription_failure_becomes_retryable_job(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    media_id = seed_voice(db)
    media_path = tmp_path / "voice.ogg"
    media_path.write_text("audio", encoding="utf-8")
    db.update_media_downloaded(media_id, local_path=str(media_path), sha256="abc", size_bytes=5)

    result = TranscriptionService(db).run_pending()

    assert result["failed"] == 1
    with db.connect() as conn:
        row = conn.execute("SELECT status, retryable, error FROM jobs WHERE stage = 'transcription' ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "failed"
    assert row["retryable"] == 1
    assert "No sidecar transcript" in row["error"]
