from __future__ import annotations

import json
import zipfile
from pathlib import Path

from tg_recall.backup import create_backup, restore_backup
from tg_recall.cli import main
from tg_recall.config import AppConfig, load_config, save_config
from tg_recall.models import ChatRecord
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


def test_backup_excludes_session_by_default_and_restores_new_profile(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = AppConfig.default(home, "default")
    config.ensure_dirs()
    save_config(config, home=home)
    Path(config.telegram.session_path).write_text("session", encoding="utf-8")
    db = Database(config.db_path)
    db.migrate()
    db.upsert_chat(ChatRecord(chat_id=10, title="Work", chat_type="group"))
    backup_path = tmp_path / "essential.zip"

    create_backup(config, backup_path)
    with zipfile.ZipFile(backup_path) as archive:
        assert "profile/telegram.session" not in archive.namelist()
        assert "profile/archive.sqlite3" in archive.namelist()
    restored = restore_backup(backup_path, home=home, profile="restored")

    assert restored["profile"] == "restored"
    assert Database(AppPaths.resolve(home, "restored").db_path).health()["counts"]["chats"] == 1


def test_global_cli_options_are_accepted_after_subcommands(tmp_path: Path, capsys) -> None:
    home = tmp_path / "home"
    assert main(["setup", "--home", str(home), "--profile", "work", "--json"]) == 0
    capsys.readouterr()

    assert main(["doctor", "--home", str(home), "--profile", "work", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["profile"] == "work"


def test_media_jobs_are_deduplicated(tmp_path: Path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    media_id = db.enqueue_media(10, 20, "voice", "file", transcription_policy="auto")

    assert db.enqueue_media(10, 20, "voice", "file", transcription_policy="auto") == media_id
    assert len(db.get_pending_jobs("media_download")) == 1


def test_cli_surface_is_the_small_core(capsys) -> None:
    import argparse

    from tg_recall.cli import build_parser

    commands = next(action for action in build_parser()._actions if isinstance(action, argparse._SubParsersAction)).choices
    assert set(commands) == {
        "setup", "doctor", "config", "telegram", "chats", "sync", "search", "read", "export",
        "media", "transcribe", "jobs", "index", "security", "backup", "purge", "stats", "usage",
    }
