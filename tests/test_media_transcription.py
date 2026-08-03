from __future__ import annotations

from datetime import UTC, datetime

from tg_ecosystem.media import MediaDownloader, MediaStore, copy_file_download
from tg_ecosystem.models import ChatRecord, MessageRecord
from tg_ecosystem.storage import Database
from tg_ecosystem.transcription import SidecarTextProvider, TranscriptionService


def seed_media_job(db: Database) -> int:
    db.migrate()
    db.upsert_chat(ChatRecord(chat_id=10, title="Work", chat_type="group"))
    db.upsert_message(
        MessageRecord(
            chat_id=10,
            message_id=20,
            date=datetime(2026, 1, 1, tzinfo=UTC),
            text="voice",
            has_media=True,
            media_type="voice",
        )
    )
    return db.enqueue_media(10, 20, "voice", "file-20")


def test_media_downloader_deduplicates_and_enqueues_transcription(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    media_id = seed_media_job(db)
    source = tmp_path / "voice.ogg"
    source.write_text("audio bytes", encoding="utf-8")

    result = MediaDownloader(db, MediaStore(tmp_path / "media"), copy_file_download(source)).run_pending()

    assert result["completed"] == 1
    media = db.get_media(media_id)
    assert media is not None
    assert media.status == "downloaded"
    assert media.sha256 is not None
    assert db.get_pending_jobs("transcription")


def test_transcription_sidecar_provider_indexes_transcript(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    media_id = seed_media_job(db)
    media_file = tmp_path / "voice.ogg"
    media_file.write_text("audio bytes", encoding="utf-8")
    media_file.with_suffix(".ogg.txt").write_text("Launch transcript deadline", encoding="utf-8")
    db.update_media_downloaded(media_id, local_path=str(media_file), sha256="abc", size_bytes=10)

    result = TranscriptionService(db, fallback_provider=SidecarTextProvider()).run_pending()

    assert result["completed"] == 1
    hits = db.search("Launch")
    assert hits[0].transcript_id is not None
