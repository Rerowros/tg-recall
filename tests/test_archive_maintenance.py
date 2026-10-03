from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

import tg_recall.storage as storage_module
from tg_recall.models import ChatRecord, MessageRecord, SearchFilters
from tg_recall.cli import main
from tg_recall.config import AppConfig, save_config
from tg_recall.storage import Database, Migration, SCHEMA_VERSION, SchemaCompatibilityError


def seed(db: Database) -> int:
    db.migrate()
    db.upsert_chat(ChatRecord(chat_id=10, title="Work", chat_type="group"))
    db.upsert_message(MessageRecord(chat_id=10, message_id=1, date=datetime(2026, 1, 2, tzinfo=UTC), text="needle voice", sender_id=7, has_media=True, media_type="voice", links_json='["https://example.test"]'))
    db.upsert_message(MessageRecord(chat_id=10, message_id=2, date=datetime(2026, 1, 3, tzinfo=UTC), text="needle document", sender_id=8, has_media=True, media_type="document"))
    media_id = db.enqueue_media(10, 1, "voice", "voice-1")
    db.insert_transcript(media_id, "test", "needle transcript")
    return media_id


def test_migration_history_bootstraps_legacy_identity_and_repeats(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    seed(db)
    with db.connect() as conn:
        before = conn.execute("SELECT id, chat_id, message_id FROM messages").fetchall()
        conn.execute("DELETE FROM schema_migrations")
    db.migrate()
    db.migrate()
    with db.connect() as conn:
        after = conn.execute("SELECT id, chat_id, message_id FROM messages").fetchall()
        history = conn.execute("SELECT version, checksum, applied_at FROM schema_migrations ORDER BY version").fetchall()
    assert [tuple(row) for row in before] == [tuple(row) for row in after]
    assert [row["version"] for row in history] == list(range(2, SCHEMA_VERSION + 1))
    assert all(row["checksum"] and row["applied_at"] for row in history)


def test_migration_failure_rolls_back_and_newer_schema_is_not_downgraded(tmp_path, monkeypatch) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    original_registry = storage_module.MIGRATION_REGISTRY
    bad = Migration(SCHEMA_VERSION + 1, "rollback-test", ("CREATE TABLE must_rollback (id INTEGER)", "INSERT INTO missing_table VALUES (1)"))
    monkeypatch.setattr(storage_module, "SCHEMA_VERSION", SCHEMA_VERSION + 1)
    monkeypatch.setattr(storage_module, "MIGRATION_REGISTRY", (*original_registry, bad))
    with pytest.raises(Exception, match="missing_table"):
        db.migrate()
    with db.connect() as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'must_rollback'").fetchone() is None

    monkeypatch.setattr(storage_module, "SCHEMA_VERSION", SCHEMA_VERSION)
    monkeypatch.setattr(storage_module, "MIGRATION_REGISTRY", original_registry)
    with db.connect() as conn:
        conn.execute("INSERT INTO schema_migrations(version, checksum, applied_at) VALUES (99, 'future', ?)", (datetime.now(UTC).isoformat(),))
        before_master = [tuple(row) for row in conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name")]
        before_columns = [tuple(row) for row in conn.execute("PRAGMA table_info(messages)")]
    with pytest.raises(SchemaCompatibilityError, match="newer"):
        db.migrate()
    with db.connect() as conn:
        after_master = [tuple(row) for row in conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name")]
        after_columns = [tuple(row) for row in conn.execute("PRAGMA table_info(messages)")]
    assert before_master == after_master
    assert before_columns == after_columns


def test_malformed_migration_registry_fails_before_schema_writes(tmp_path, monkeypatch) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    with db.connect() as conn:
        before = [tuple(row) for row in conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name")]
    malformed = (storage_module.MIGRATION_REGISTRY[0], Migration(SCHEMA_VERSION + 1, "skips-v3", ("CREATE TABLE should_not_exist (id INTEGER)",)))
    monkeypatch.setattr(storage_module, "SCHEMA_VERSION", SCHEMA_VERSION + 1)
    monkeypatch.setattr(storage_module, "MIGRATION_REGISTRY", malformed)
    with pytest.raises(SchemaCompatibilityError, match="registry"):
        db.migrate()
    with db.connect() as conn:
        after = [tuple(row) for row in conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name")]
    assert before == after


def test_job_filters_retry_and_dry_run_repair_keep_permanent_and_backoff_jobs(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    media_id = seed(db)
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status = 'retry', retryable = 1, retry_after = '2999-01-01T00:00:00+00:00' WHERE media_id = ?", (media_id,))
        future_id = conn.execute("SELECT id FROM jobs WHERE media_id = ?", (media_id,)).fetchone()[0]
        permanent_id = conn.execute("INSERT INTO jobs(stage, status, chat_id, retryable, error, payload_json, created_at, updated_at) VALUES ('transcription', 'failed', 10, 0, 'permanent', '{}', ?, ?)", (datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat())).lastrowid
        stale_id = conn.execute("INSERT INTO jobs(stage, status, chat_id, retryable, payload_json, created_at, updated_at) VALUES ('transcription', 'processing', 10, 0, '{}', '2020-01-01T00:00:00+00:00', '2020-01-01T00:00:00+00:00')").lastrowid

    listed = db.list_jobs(stage="transcription", status="failed", retryable=False, chat_id=10, limit=10)
    assert [job["id"] for job in listed] == [permanent_id]
    retried = db.retry_jobs([future_id, permanent_id])
    assert retried["changed"] == 0
    assert retried["skipped"] == {"backoff_active": 1, "permanent_failure": 1}
    preview = db.repair_jobs()
    assert preview["dry_run"] is True
    assert any(item["job_id"] == stale_id for item in preview["proposed"])
    assert db.list_jobs(status="processing", limit=10)
    applied = db.repair_jobs(apply=True)
    assert applied["applied"] >= 1
    assert not db.list_jobs(status="processing", limit=10)
    with db.connect() as conn:
        audit = json.loads(conn.execute("SELECT details_json FROM audit_events WHERE event_type = 'jobs_repaired' ORDER BY id DESC LIMIT 1").fetchone()[0])
    assert stale_id in audit["affected"]["job_ids"]
    assert audit["actions"]


def test_queue_sanitizes_errors_and_never_repairs_failed_prior_queue_entries(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    media_id = seed(db)
    secret_error = "api_key=super-secret C:\\private\\message.txt quoted archive text"
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status = 'failed', retryable = 0, error = ? WHERE media_id = ?", (secret_error, media_id))
    job = db.list_jobs(stage="media_download", limit=10)[0]
    assert job["error"] == "operation_failed"
    assert "secret" not in job["error"] and "private" not in job["error"]
    repair = db.repair_jobs()
    assert not any(item["action"] == "enqueue_missing_media_download" and item["media_id"] == media_id for item in repair["proposed"])


def test_retry_timestamp_and_bounds_are_validated(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    media_id = seed(db)
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status = 'retry', retryable = 1, retry_after = 'not-a-time' WHERE media_id = ?", (media_id,))
        job_id = conn.execute("SELECT id FROM jobs WHERE media_id = ?", (media_id,)).fetchone()[0]
    assert db.retry_jobs([job_id])["skipped"] == {"invalid_retry_after": 1}
    assert any(item["action"] == "normalize_invalid_retry_after" for item in db.repair_jobs()["proposed"])
    with pytest.raises(ValueError, match="limited"):
        db.retry_jobs(list(range(1, 102)))
    with pytest.raises(ValueError, match="older_than"):
        db.list_jobs(older_than="bad-time")
    with pytest.raises(ValueError, match="stale_after_hours"):
        db.repair_jobs(stale_after_hours=0)


def test_job_age_filter_normalizes_non_utc_offsets(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    media_id = seed(db)
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET updated_at = '2026-01-01T00:30:00+00:00' WHERE media_id = ?", (media_id,))
    # 03:00+03 is exactly 00:00 UTC: a 00:30 UTC job is not older.
    assert db.list_jobs(older_than="2026-01-01T03:00:00+03:00", limit=10) == []


def test_filter_contract_matches_keyword_token_export_and_context(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    seed(db)
    filters = SearchFilters(chat_id=10, sender_id=7, since="2026-01-01", until="2026-01-02T23:59:59+00:00", media_type="voice", has_link=True)

    exported = db.export_messages(filters, limit=10)
    assert {item["message_id"] for item in exported} == {1}


def test_doctor_and_jobs_cli_keep_sanitized_additive_json(tmp_path, capsys) -> None:
    home = tmp_path / "home"
    config = AppConfig.default(home)
    save_config(config, home=home)
    db = Database(config.db_path)
    seed(db)

    assert main(["--home", str(home), "--json", "doctor"]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["maintenance"]["schema"]["status"] == "ok"
    assert "backup_guidance" in doctor

    assert main(["--home", str(home), "--json", "jobs", "--stage", "media_download", "--limit", "10"]) == 0
    jobs = json.loads(capsys.readouterr().out)
    assert jobs and {"id", "stage", "status", "retryable", "updated_at"} <= jobs[0].keys()


def test_jobs_cli_inspects_failed_work_and_retries_only_selected_job(tmp_path, capsys) -> None:
    home = tmp_path / "home"
    config = AppConfig.default(home)
    save_config(config, home=home)
    db = Database(config.db_path)
    media_id = seed(db)
    with db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'failed', retryable = 1, error = 'temporary rate limit' WHERE media_id = ?",
            (media_id,),
        )
        job_id = conn.execute("SELECT id FROM jobs WHERE media_id = ?", (media_id,)).fetchone()[0]

    assert main([
        "--home", str(home), "--json", "jobs", "--stage", "media_download", "--status", "failed",
        "--retryable", "true", "--chat-id", "10", "--limit", "10",
    ]) == 0
    failed = json.loads(capsys.readouterr().out)
    assert len(failed) == 1
    assert {
        "id": job_id,
        "stage": "media_download",
        "status": "failed",
        "chat_id": 10,
        "message_id": 1,
        "media_id": media_id,
        "error": "transient_provider_error",
        "retryable": 1,
        "retry_after": None,
    }.items() <= failed[0].items()
    assert failed[0]["created_at"] and failed[0]["updated_at"]

    assert main(["--home", str(home), "--json", "jobs", "--retry", str(job_id)]) == 0
    assert json.loads(capsys.readouterr().out) == {"selected": [job_id], "changed": 1, "skipped": {}}
