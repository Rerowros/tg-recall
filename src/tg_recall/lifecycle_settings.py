"""Global, profile-free settings for optional release-update notices."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .paths import AppRoots
from .security import harden_path


UPDATE_SETTINGS_SCHEMA_VERSION = 1
_MIN_INTERVAL_HOURS = 1
_MAX_INTERVAL_HOURS = 24 * 30


@dataclass(frozen=True)
class UpdateCheckSettings:
    enabled: bool = False
    interval_hours: int = 24

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("update checks enabled flag must be boolean")
        if not isinstance(self.interval_hours, int) or isinstance(self.interval_hours, bool):
            raise ValueError("update check interval must be an integer")
        if not _MIN_INTERVAL_HOURS <= self.interval_hours <= _MAX_INTERVAL_HOURS:
            raise ValueError(
                f"update check interval must be between {_MIN_INTERVAL_HOURS} and {_MAX_INTERVAL_HOURS} hours"
            )

    def as_json(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "interval_hours": self.interval_hours,
            "schema_version": UPDATE_SETTINGS_SCHEMA_VERSION,
        }


class UpdateSettingsStore:
    """Store only opt-in cadence, never profile or Telegram data."""

    def __init__(self, roots: AppRoots):
        self.roots = roots

    @property
    def path(self) -> Path:
        return self.roots.state / "updates" / "v1" / "settings.json"

    def load(self) -> UpdateCheckSettings:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or set(raw) != {"enabled", "interval_hours", "schema_version"}:
                raise ValueError("unsupported update settings schema")
            if raw["schema_version"] != UPDATE_SETTINGS_SCHEMA_VERSION:
                raise ValueError("unsupported update settings schema")
            return UpdateCheckSettings(enabled=raw["enabled"], interval_hours=raw["interval_hours"])
        except FileNotFoundError:
            return UpdateCheckSettings()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return UpdateCheckSettings()

    def save(self, settings: UpdateCheckSettings) -> Path:
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        _require_private(directory, is_dir=True)
        payload = (
            json.dumps(settings.as_json(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        fd, temporary_name = tempfile.mkstemp(prefix=".settings.", suffix=".tmp", dir=directory)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            _require_private(temporary, is_dir=False)
            os.replace(temporary, self.path)
            _require_private(self.path, is_dir=False)
        finally:
            temporary.unlink(missing_ok=True)
        return self.path


def _require_private(path: Path, *, is_dir: bool) -> None:
    finding = harden_path(path, is_dir=is_dir)
    if finding.status != "ok":
        raise OSError(f"could not restrict update settings path: {finding.detail}")
