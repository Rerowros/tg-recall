from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from .config import AppConfig, load_config, save_config
from .media import MediaStore, sha256_file
from .paths import AppPaths
from .security import harden_path
from .storage import Database


def inspect_legacy(source: str | Path) -> dict[str, Any]:
    root = Path(source).expanduser().resolve()
    config_path = root / "config.json"
    raw = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    db_path = _legacy_path(raw.get("db_path"), root / "archive.sqlite3", root)
    media_dir = _legacy_path(raw.get("media_dir"), root / "media", root)
    session_path = _legacy_path(raw.get("telegram", {}).get("session_path"), root / "telegram.session", root)
    return {
        "root": str(root),
        "config": str(config_path) if config_path.exists() else None,
        "database": str(db_path) if db_path.exists() else None,
        "media_dir": str(media_dir) if media_dir.exists() else None,
        "session": str(session_path) if session_path.exists() else None,
        "bytes": _directory_size(root),
    }


def migrate_legacy(
    source: str | Path,
    *,
    home: str | Path | None,
    profile: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    inventory = inspect_legacy(source)
    if dry_run:
        return {"dry_run": True, **inventory}
    if not inventory["database"]:
        raise FileNotFoundError("legacy archive.sqlite3 was not found")

    source_root = Path(inventory["root"])
    paths = AppPaths.resolve(home, profile)
    if paths.profile_data_dir.exists() and any(paths.profile_data_dir.iterdir()):
        raise FileExistsError(f"target profile '{profile}' already contains data")
    paths.ensure_dirs()
    legacy_cfg = load_config(source_root / "config.json")
    target_cfg = AppConfig.default(home, profile)
    target_cfg.telegram.api_id = legacy_cfg.telegram.api_id
    target_cfg.telegram.api_hash = legacy_cfg.telegram.api_hash
    target_cfg.telegram.phone = legacy_cfg.telegram.phone
    target_cfg.provider_policy = legacy_cfg.provider_policy
    target_cfg.ai_access = legacy_cfg.ai_access
    target_cfg.llm = legacy_cfg.llm
    target_cfg.semantic = legacy_cfg.semantic
    target_cfg.ensure_dirs()
    _sqlite_backup(Path(inventory["database"]), paths.db_path)
    db = Database(paths.db_path)
    db.migrate()
    media_root = Path(inventory["media_dir"]) if inventory["media_dir"] else None
    copied_media = _migrate_media(db, media_root, paths.media_dir)
    if inventory["session"]:
        shutil.copy2(Path(inventory["session"]), paths.session_path)
    save_config(target_cfg, home=home)
    for target in (paths.profile_data_dir, paths.db_path, paths.session_path, paths.credentials_path):
        if target.exists():
            harden_path(target, is_dir=target.is_dir())
    return {"dry_run": False, "profile": profile, "path": str(paths.profile_data_dir), "media": copied_media, "health": db.health()}


def _migrate_media(db: Database, legacy_media_dir: Path | None, target_media_dir: Path) -> int:
    if legacy_media_dir is None:
        return 0
    store = MediaStore(target_media_dir)
    copied = 0
    with db.connect() as conn:
        rows = conn.execute("SELECT id, sha256, local_path FROM media WHERE local_path IS NOT NULL").fetchall()
        for row in rows:
            source = Path(row["local_path"])
            if not source.is_absolute():
                source = legacy_media_dir / source
            if not source.exists() or not source.is_file():
                continue
            digest = row["sha256"] or sha256_file(source)
            key = store.storage_key_for_sha256(digest, source.suffix)
            target = store.path_for_storage_key(key)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(source, target)
                copied += 1
            conn.execute("UPDATE media SET storage_key = ?, local_path = NULL, sha256 = ? WHERE id = ?", (key, digest, row["id"]))
    return copied


def _legacy_path(value: str | None, fallback: Path, root: Path) -> Path:
    if not value:
        return fallback
    path = Path(value)
    return path if path.is_absolute() else root / path


def _sqlite_backup(source: Path, destination: Path) -> None:
    source_conn = sqlite3.connect(source)
    destination_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(destination_conn)
    finally:
        destination_conn.close()
        source_conn.close()


def _directory_size(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
