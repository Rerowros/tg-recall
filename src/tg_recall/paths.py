from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir, user_data_dir, user_state_dir


APP_NAME = "tg-recall"
DEFAULT_PROFILE = "default"
PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def validate_profile(value: str) -> str:
    if not PROFILE_RE.fullmatch(value):
        raise ValueError("profile must contain only letters, digits, '.', '_' or '-'")
    return value


@dataclass(frozen=True)
class AppRoots:
    config: Path
    data: Path
    state: Path
    cache: Path
    portable: bool = False

    @classmethod
    def resolve(cls, home: str | Path | None = None) -> "AppRoots":
        selected_home = home or os.environ.get("TG_RECALL_HOME")
        if selected_home:
            root = Path(selected_home).expanduser().resolve()
            return cls(root / "config", root / "data", root / "state", root / "cache", portable=True)

        # Archive data must not use APPDATA/Roaming. Windows keeps all roots below
        # LOCALAPPDATA; Linux follows XDG's separate config/data/state/cache roots.
        if os.name == "nt":
            root = Path(user_data_dir(APP_NAME, appauthor=False, roaming=False)).resolve()
            return cls(root / "config", root / "data", root / "state", root / "cache")
        return cls(
            Path(user_config_dir(APP_NAME, appauthor=False)).resolve(),
            Path(user_data_dir(APP_NAME, appauthor=False)).resolve(),
            Path(user_state_dir(APP_NAME, appauthor=False)).resolve(),
            Path(user_cache_dir(APP_NAME, appauthor=False)).resolve(),
        )


@dataclass(frozen=True)
class AppPaths:
    roots: AppRoots
    profile: str = DEFAULT_PROFILE

    @classmethod
    def resolve(cls, home: str | Path | None = None, profile: str | None = None) -> "AppPaths":
        selected_profile = profile or os.environ.get("TG_RECALL_PROFILE") or DEFAULT_PROFILE
        return cls(AppRoots.resolve(home), validate_profile(selected_profile))

    @property
    def config_path(self) -> Path:
        return self.roots.config / "config.json"

    @property
    def profile_config_path(self) -> Path:
        return self.roots.config / "profiles" / f"{self.profile}.json"

    @property
    def credentials_path(self) -> Path:
        return self.roots.config / "credentials" / f"{self.profile}.json"

    @property
    def profile_data_dir(self) -> Path:
        return self.roots.data / "profiles" / self.profile

    @property
    def db_path(self) -> Path:
        return self.profile_data_dir / "archive.sqlite3"

    @property
    def session_path(self) -> Path:
        return self.profile_data_dir / "telegram.session"

    @property
    def media_dir(self) -> Path:
        return self.profile_data_dir / "objects"

    @property
    def wiki_dir(self) -> Path:
        return self.profile_data_dir / "wiki"

    @property
    def wiki_raw_dir(self) -> Path:
        return self.wiki_dir / "raw"

    @property
    def wiki_pages_dir(self) -> Path:
        return self.wiki_dir / "pages"

    @property
    def wiki_revisions_dir(self) -> Path:
        return self.wiki_dir / "revisions"

    @property
    def exports_dir(self) -> Path:
        return self.profile_data_dir / "exports"

    @property
    def profile_state_dir(self) -> Path:
        return self.roots.state / "profiles" / self.profile

    @property
    def logs_dir(self) -> Path:
        return self.profile_state_dir / "logs"

    @property
    def locks_dir(self) -> Path:
        return self.profile_state_dir / "locks"

    @property
    def profile_cache_dir(self) -> Path:
        return self.roots.cache / "profiles" / self.profile

    @property
    def downloads_dir(self) -> Path:
        return self.profile_cache_dir / "downloads"

    @property
    def extracted_audio_dir(self) -> Path:
        return self.profile_cache_dir / "extracted-audio"

    @property
    def transcription_cache_dir(self) -> Path:
        return self.profile_cache_dir / "transcription"

    def ensure_dirs(self) -> None:
        for path in (
            self.roots.config,
            self.profile_config_path.parent,
            self.credentials_path.parent,
            self.profile_data_dir,
            self.media_dir,
            self.wiki_raw_dir,
            self.wiki_pages_dir,
            self.wiki_revisions_dir,
            self.exports_dir,
            self.logs_dir,
            self.locks_dir,
            self.downloads_dir,
            self.extracted_audio_dir,
            self.transcription_cache_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
