from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from tg_recall.backup import create_backup, restore_backup
from tg_recall.config import AppConfig, save_config
from tg_recall.paths import AppPaths
from tg_recall.storage import Database, SCHEMA_VERSION


SHA = "a" * 64


def test_wiki_migration_stores_only_private_logical_metadata(tmp_path: Path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    db.record_wiki_snapshot(
        snapshot_id="snapshot-1", profile_id="work", scope_id="team", scope_hash=SHA,
        source_version="messages-v1", source_cursor=("2026-08-01T00:00:00Z", "tg://chat/10/message/1", "message", "message-1"),
        raw_path="raw/work/team/snapshot-1.jsonl", raw_sha256=SHA, record_count=1, created_at="2026-08-01T00:00:00Z",
    )
    db.record_wiki_page_revision(
        revision_id="revision-1", snapshot_id="snapshot-1", profile_id="work", scope_id="team", page_kind="people", subject_id="alice",
        page_path="revisions/work/team/revision-1.md", page_sha256=SHA, source_version="wiki-v1", freshness_at="2026-08-01T00:00:00Z", updated_at="2026-08-01T00:00:00Z",
        assertions=[{"assertion_id": "assertion-1", "kind": "observed", "confidence": 1.0, "citations": ["tg://chat/10/message/1"]}],
    )
    metadata = db.wiki_metadata(profile_id="work", scope_id="team")
    assert metadata["snapshots"][0]["raw_path"] == "raw/work/team/snapshot-1.jsonl"
    assert metadata["revisions"][0]["page_path"] == "revisions/work/team/revision-1.md"
    with db.connect() as conn:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(wiki_assertions)")}
        citation_version = conn.execute("SELECT source_version FROM wiki_assertion_citations WHERE assertion_id = 'assertion-1'").fetchone()[0]
    assert {"wiki_snapshots", "wiki_page_revisions", "wiki_assertions", "wiki_assertion_citations"} <= tables
    assert "text" not in columns and "markdown" not in columns
    assert citation_version == "messages-v1"
    assert SCHEMA_VERSION >= 4
    with pytest.raises(ValueError, match="relative"):
        db.record_wiki_snapshot(
            snapshot_id="snapshot-2", profile_id="work", scope_id="team", scope_hash=SHA,
            source_version="v1", source_cursor=("2026-08-01T00:00:00Z", "tg://chat/10/message/1", "message", "message-1"),
            raw_path="C:/private/raw.jsonl", raw_sha256=SHA, record_count=1, created_at="2026-08-01T00:00:00Z",
        )
    with pytest.raises(ValueError, match="SHA-256"):
        db.record_wiki_snapshot(
            snapshot_id="snapshot-upper", profile_id="work", scope_id="team", scope_hash=SHA.upper(),
            source_version="v1", source_cursor=("2026-08-01T00:00:00Z", "tg://chat/10/message/1", "message", "message-1"),
            raw_path="raw/work/team/snapshot-upper.jsonl", raw_sha256=SHA, record_count=1, created_at="2026-08-01T00:00:00Z",
        )
    with pytest.raises(ValueError, match="POSIX"):
        db.record_wiki_snapshot(
            snapshot_id="snapshot-path", profile_id="work", scope_id="team", scope_hash=SHA,
            source_version="v1", source_cursor=("2026-08-01T00:00:00Z", "tg://chat/10/message/1", "message", "message-1"),
            raw_path="raw\\work\\snapshot.jsonl", raw_sha256=SHA, record_count=1, created_at="2026-08-01T00:00:00Z",
        )

    with pytest.raises(ValueError, match="citations"):
        db.record_wiki_page_revision(
            revision_id="revision-invalid", snapshot_id="snapshot-1", profile_id="work", scope_id="team", page_kind="people", subject_id="alice",
            page_path="revisions/work/team/revision-invalid.md", page_sha256=SHA, source_version="wiki-v1", freshness_at="2026-08-01T00:00:00Z", updated_at="2026-08-01T00:00:00Z",
            assertions=[{"assertion_id": "assertion-invalid", "kind": "observed", "confidence": 1.0, "citations": []}],
        )
    with db.connect() as conn:
        assert conn.execute("SELECT 1 FROM wiki_page_revisions WHERE revision_id = 'revision-invalid'").fetchone() is None

    with pytest.raises(ValueError, match="unique"):
        db.record_wiki_page_revision(
            revision_id="revision-duplicate", snapshot_id="snapshot-1", profile_id="work", scope_id="team", page_kind="people", subject_id="alice",
            page_path="revisions/work/team/revision-duplicate.md", page_sha256=SHA, source_version="wiki-v1", freshness_at="2026-08-01T00:00:00Z", updated_at="2026-08-01T00:00:00Z",
            assertions=[
                {"assertion_id": "assertion-duplicate", "kind": "observed", "confidence": 1.0, "citations": ["tg://chat/10/message/1"]},
                {"assertion_id": "assertion-duplicate", "kind": "hypothesis", "confidence": 0.5, "citations": ["tg://chat/10/message/2"]},
            ],
        )
    with db.connect() as conn:
        assert conn.execute("SELECT 1 FROM wiki_page_revisions WHERE revision_id = 'revision-duplicate'").fetchone() is None


def test_profile_wiki_paths_are_portable_private_and_restored_with_backup(tmp_path: Path) -> None:
    home = tmp_path / "portable"
    paths = AppPaths.resolve(home, "work")
    config = AppConfig.default(home, "work")
    config.ensure_dirs()
    save_config(config, home=home)
    assert paths.wiki_raw_dir == home / "data" / "profiles" / "work" / "wiki" / "raw"
    assert paths.wiki_pages_dir.exists() and paths.wiki_revisions_dir.exists()
    (paths.wiki_raw_dir / "snapshot.jsonl").write_text('{"private":true}\n', encoding="utf-8")
    (paths.wiki_pages_dir / "alice.md").write_text("# private\n", encoding="utf-8")
    db = Database(config.db_path)
    db.migrate()
    backup = tmp_path / "essential.zip"
    create_backup(config, backup)
    with zipfile.ZipFile(backup) as archive:
        assert "profile/wiki/raw/snapshot.jsonl" in archive.namelist()
        assert "profile/wiki/pages/alice.md" in archive.namelist()
    restore_backup(backup, home=home, profile="restored")
    restored = AppPaths.resolve(home, "restored")
    assert (restored.wiki_raw_dir / "snapshot.jsonl").read_text(encoding="utf-8") == '{"private":true}\n'
    assert "wiki/" in Path(".gitignore").read_text(encoding="utf-8")
