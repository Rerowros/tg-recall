from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from tg_recall.backup import create_backup, restore_backup
from tg_recall.cli import main
from tg_recall.config import AppConfig, load_config, save_config
from tg_recall.media import MediaStore
from tg_recall.migration import migrate_legacy
from tg_recall.models import ChatRecord, MessageRecord
from tg_recall.paths import AppPaths
from tg_recall.storage import Database


def test_portable_profile_paths_are_isolated(tmp_path: Path) -> None:
    home = tmp_path / "portable"
    first = AppPaths.resolve(home, "first")
    second = AppPaths.resolve(home, "second")

    assert first.db_path != second.db_path
    assert first.roots.config == home / "config"
    assert first.media_dir == home / "data" / "profiles" / "first" / "objects"

    config = AppConfig.default(home, "first")
    config.telegram.api_id = 123
    config.telegram.api_hash = "secret"
    save_config(config, home=home)
    loaded = load_config(home=home, profile="first")

    assert loaded.db_path == str(first.db_path)
    assert loaded.telegram.api_hash == "secret"
    assert not (home / "config" / "profiles" / "first.json").read_text(encoding="utf-8").__contains__("secret")


def test_scope_media_policy_and_job_deduplication(tmp_path: Path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    db.create_scope("work", [10], "2026-01-01", None, "voice,photo", "auto")
    media_id = db.enqueue_media(10, 20, "voice", "file", transcription_policy="auto")
    assert db.enqueue_media(10, 20, "voice", "file", transcription_policy="auto") == media_id

    assert db.get_scope("work") == {
        "name": "work",
        "since": "2026-01-01",
        "until": None,
        "media_policy": "voice,photo",
        "transcription_policy": "auto",
        "chat_ids": [10],
    }
    assert len(db.get_pending_jobs("media_download")) == 1


def test_legacy_migration_copies_source_and_normalizes_media_paths(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    legacy_media = legacy / "media"
    legacy_media.mkdir(parents=True)
    media_file = legacy_media / "voice.ogg"
    media_file.write_bytes(b"voice")
    db_path = legacy / "archive.sqlite3"
    legacy_db = Database(db_path)
    legacy_db.migrate()
    legacy_db.upsert_chat(ChatRecord(chat_id=10, title="Work", chat_type="group"))
    legacy_db.upsert_message(
        MessageRecord(chat_id=10, message_id=20, date=datetime(2026, 1, 1, tzinfo=UTC), text="voice", has_media=True, media_type="voice")
    )
    media_id = legacy_db.enqueue_media(10, 20, "voice", "file")
    legacy_db.update_media_downloaded(media_id, local_path=str(media_file), sha256="a" * 64, size_bytes=5)
    (legacy / "config.json").write_text(
        json.dumps({"db_path": str(db_path), "media_dir": str(legacy_media), "telegram": {"session_path": str(legacy / "telegram.session")}}),
        encoding="utf-8",
    )

    target_home = tmp_path / "new-home"
    preview = migrate_legacy(legacy, home=target_home, profile="default", dry_run=True)
    result = migrate_legacy(legacy, home=target_home, profile="default")
    migrated = Database(AppPaths.resolve(target_home, "default").db_path)
    record = migrated.get_media(media_id)
    store = MediaStore(AppPaths.resolve(target_home, "default").media_dir)

    assert preview["dry_run"] is True
    assert result["media"] == 1
    assert media_file.exists()
    assert record is not None and record.storage_key is not None and record.local_path is None
    assert store.path_for_media(record).exists()
    assert migrated.health()["integrity"] == "ok"


def test_backup_excludes_session_by_default_and_restores_new_profile(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = AppConfig.default(home, "default")
    config.ensure_dirs()
    save_config(config, home=home)
    Path(config.telegram.session_path).write_text("session", encoding="utf-8")
    db = Database(config.db_path)
    db.migrate()
    db.upsert_chat(ChatRecord(chat_id=10, title="Work", chat_type="group"))
    Path(config.wiki_dir, "pages", "person.md").write_text("private wiki", encoding="utf-8")
    backup_path = tmp_path / "essential.zip"

    create_backup(config, backup_path)
    with zipfile.ZipFile(backup_path) as archive:
        assert "profile/telegram.session" not in archive.namelist()
        assert "profile/archive.sqlite3" in archive.namelist()
    restored = restore_backup(backup_path, home=home, profile="restored")

    assert restored["profile"] == "restored"
    assert Database(AppPaths.resolve(home, "restored").db_path).health()["counts"]["chats"] == 1


def test_agent_guide_is_json_and_does_not_expose_secrets(tmp_path: Path, capsys) -> None:
    exit_code = main(["--home", str(tmp_path / "home"), "--json", "setup"])
    assert exit_code == 0
    capsys.readouterr()

    exit_code = main(["--home", str(tmp_path / "home"), "agent", "guide", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert "sync ensure" in payload["guide"]
    assert "session_path" not in json.dumps(payload)


def test_global_cli_options_are_accepted_after_subcommands(tmp_path: Path, capsys) -> None:
    home = tmp_path / "home"
    assert main(["setup", "--home", str(home), "--profile", "work", "--json"]) == 0
    capsys.readouterr()

    assert main(["doctor", "--home", str(home), "--profile", "work", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["profile"] == "work"
