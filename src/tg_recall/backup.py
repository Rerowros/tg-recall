from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from .config import AppConfig
from .paths import AppPaths
from .security import harden_path


def create_backup(
    config: AppConfig,
    output: str | Path,
    *,
    mode: str = "essential",
    include_session: bool = False,
) -> dict[str, object]:
    if mode not in {"essential", "full"}:
        raise ValueError("backup mode must be 'essential' or 'full'")
    destination = Path(output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination == Path(config.db_path).resolve() or destination.is_relative_to(Path(config.data_dir).resolve()):
        raise ValueError("backup output must be outside the active profile data directory")

    config_root = Path(config.credentials_path).parent.parent
    profile_config = config_root / "profiles" / f"{config.profile}.json"
    credentials_path = Path(config.credentials_path)
    with tempfile.TemporaryDirectory(prefix="tg-recall-backup-") as temporary:
        staging = Path(temporary)
        db_copy = staging / "archive.sqlite3"
        _sqlite_backup(Path(config.db_path), db_copy)
        manifest = {
            "format": "tg-recall-backup",
            "version": 1,
            "profile": config.profile,
            "mode": mode,
            "include_session": include_session,
            "created_at": datetime.now(UTC).isoformat(),
        }
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
            archive.write(db_copy, "profile/archive.sqlite3")
            _write_tree(archive, Path(config.wiki_dir), "profile/wiki")
            if profile_config.exists():
                archive.write(profile_config, f"config/profiles/{profile_config.name}")
            if mode == "full":
                _write_tree(archive, Path(config.media_dir), "profile/objects")
            if include_session:
                for source, name in (
                    (credentials_path, f"config/credentials/{credentials_path.name}"),
                    (Path(config.telegram.session_path), "profile/telegram.session"),
                ):
                    if source.exists():
                        archive.write(source, name)
    harden_path(destination, is_dir=False)
    return {"path": str(destination), "mode": mode, "include_session": include_session, "bytes": destination.stat().st_size}


def restore_backup(
    archive_path: str | Path,
    *,
    home: str | Path | None,
    profile: str,
    replace: bool = False,
) -> dict[str, object]:
    source = Path(archive_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    paths = AppPaths.resolve(home, profile)
    if paths.profile_data_dir.exists() and any(paths.profile_data_dir.iterdir()) and not replace:
        raise FileExistsError(f"Profile '{profile}' already contains data; use --replace after confirmation")
    if replace:
        _clear_profile(paths)
    with zipfile.ZipFile(source) as archive:
        manifest = _read_manifest(archive)
        if manifest.get("format") != "tg-recall-backup":
            raise ValueError("not a tg-recall backup")
        for info in archive.infolist():
            _validate_member(info.filename)
        paths.ensure_dirs()
        profile_config_member = next((name for name in archive.namelist() if name.startswith("config/profiles/") and name.endswith(".json")), None)
        credentials_member = next((name for name in archive.namelist() if name.startswith("config/credentials/") and name.endswith(".json")), None)
        members = [
            ("profile/archive.sqlite3", paths.db_path),
            (profile_config_member, paths.profile_config_path),
            (credentials_member, paths.credentials_path),
            ("profile/telegram.session", paths.session_path),
        ]
        for member, target in members:
            if member is None:
                continue
            if member in archive.namelist():
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as input_file, target.open("wb") as output_file:
                    shutil.copyfileobj(input_file, output_file)
        _extract_prefix(archive, "profile/wiki/", paths.wiki_dir)
        _extract_prefix(archive, "profile/objects/", paths.media_dir)
    for target in (paths.profile_data_dir, paths.db_path, paths.session_path, paths.profile_config_path, paths.credentials_path):
        if target.exists():
            harden_path(target, is_dir=target.is_dir())
    return {"profile": profile, "source_profile": manifest.get("profile"), "path": str(paths.profile_data_dir)}


def _sqlite_backup(source: Path, destination: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"Archive database does not exist: {source}")
    source_conn = sqlite3.connect(source)
    destination_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(destination_conn)
    finally:
        destination_conn.close()
        source_conn.close()


def _write_tree(archive: zipfile.ZipFile, source: Path, prefix: str) -> None:
    if not source.exists():
        return
    for path in source.rglob("*"):
        if path.is_file():
            archive.write(path, f"{prefix}/{path.relative_to(source).as_posix()}")


def _read_manifest(archive: zipfile.ZipFile) -> dict[str, object]:
    try:
        return json.loads(archive.read("manifest.json"))
    except KeyError as exc:
        raise ValueError("backup has no manifest") from exc


def _validate_member(name: str) -> None:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe backup path: {name}")


def _extract_prefix(archive: zipfile.ZipFile, prefix: str, destination: Path) -> None:
    for member in archive.namelist():
        if not member.startswith(prefix) or member.endswith("/"):
            continue
        relative = Path(member.removeprefix(prefix))
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as input_file, target.open("wb") as output_file:
            shutil.copyfileobj(input_file, output_file)


def _clear_profile(paths: AppPaths) -> None:
    for target in (paths.profile_data_dir, paths.profile_state_dir, paths.profile_cache_dir):
        if target.exists():
            shutil.rmtree(target)
    for target in (paths.profile_config_path, paths.credentials_path):
        target.unlink(missing_ok=True)
