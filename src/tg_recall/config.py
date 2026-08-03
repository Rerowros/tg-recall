from __future__ import annotations

import json
import os
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .paths import AppPaths
from .security import harden_path


SENSITIVE_KEYS = {"api_hash", "phone", "session_path", "openai_api_key", "provider_key", "api_key", "credentials_path"}


@dataclass
class TelegramConfig:
    api_id: int | None = None
    api_hash: str | None = None
    phone: str | None = None
    session_path: str = ""


@dataclass
class ProviderPolicy:
    external_llm_enabled: bool = False
    external_transcription_enabled: bool = False
    external_embeddings_enabled: bool = False


@dataclass
class AIAccessPolicy:
    enabled: bool = False
    allowed_chat_ids: list[int] = field(default_factory=list)
    max_results: int = 5


@dataclass
class LLMConfig:
    provider: str = "extractive"
    model: str | None = None
    api_key: str | None = None


@dataclass
class SemanticConfig:
    enabled: bool = False
    provider: str = "local-token"


@dataclass
class AppConfig:
    profile: str = "default"
    data_dir: str = ""
    db_path: str = ""
    media_dir: str = ""
    state_dir: str = ""
    cache_dir: str = ""
    exports_dir: str = ""
    wiki_dir: str = ""
    credentials_path: str = ""
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    provider_policy: ProviderPolicy = field(default_factory=ProviderPolicy)
    ai_access: AIAccessPolicy = field(default_factory=AIAccessPolicy)
    llm: LLMConfig = field(default_factory=LLMConfig)
    semantic: SemanticConfig = field(default_factory=SemanticConfig)

    @classmethod
    def default(cls, home: str | Path | None = None, profile: str | None = None) -> "AppConfig":
        paths = AppPaths.resolve(home, profile)
        return cls(
            profile=paths.profile,
            data_dir=str(paths.profile_data_dir),
            db_path=str(paths.db_path),
            media_dir=str(paths.media_dir),
            state_dir=str(paths.profile_state_dir),
            cache_dir=str(paths.profile_cache_dir),
            exports_dir=str(paths.exports_dir),
            wiki_dir=str(paths.wiki_dir),
            credentials_path=str(paths.credentials_path),
            telegram=TelegramConfig(session_path=str(paths.session_path)),
        )

    def ensure_dirs(self) -> None:
        for path in (
            Path(self.data_dir),
            Path(self.media_dir),
            Path(self.state_dir),
            Path(self.cache_dir),
            Path(self.exports_dir),
            Path(self.wiki_dir) / "raw",
            Path(self.wiki_dir) / "pages",
            Path(self.wiki_dir) / "revisions",
            Path(self.telegram.session_path).parent,
            Path(self.db_path).parent,
            Path(self.credentials_path).parent,
        ):
            path.mkdir(parents=True, exist_ok=True)
            harden_path(path, is_dir=True)


def app_paths(home: str | Path | None = None, profile: str | None = None) -> AppPaths:
    return AppPaths.resolve(home, profile)


def config_path(path: str | Path | None = None, *, home: str | Path | None = None) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    legacy_home = os.environ.get("TG_ECOSYSTEM_HOME")
    if legacy_home and not home and not os.environ.get("TG_RECALL_HOME"):
        warnings.warn("TG_ECOSYSTEM_HOME is deprecated; run `tg-recall migrate legacy`", UserWarning, stacklevel=2)
        return Path(legacy_home).expanduser().resolve() / "config.json"
    return AppPaths.resolve(home).config_path


def load_config(
    path: str | Path | None = None,
    *,
    home: str | Path | None = None,
    profile: str | None = None,
) -> AppConfig:
    explicit_path = config_path(path, home=home)
    paths = AppPaths.resolve(home, profile)

    if path or (explicit_path.exists() and explicit_path != paths.config_path):
        return _load_legacy_config(explicit_path, home=home, profile=profile)

    global_raw = _read_json(paths.config_path)
    selected_profile = profile or os.environ.get("TG_RECALL_PROFILE") or global_raw.get("active_profile") or paths.profile
    paths = AppPaths.resolve(home, selected_profile)
    profile_raw = _read_json(paths.profile_config_path)
    credentials = _read_json(paths.credentials_path)
    return _from_parts(paths, profile_raw, credentials)


def save_config(
    config: AppConfig,
    path: str | Path | None = None,
    *,
    home: str | Path | None = None,
) -> Path:
    if path:
        target = config_path(path, home=home)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
        harden_path(target.parent, is_dir=True)
        harden_path(target, is_dir=False)
        return target

    paths = AppPaths.resolve(home, config.profile)
    paths.ensure_dirs()
    paths.config_path.write_text(json.dumps({"active_profile": config.profile}, indent=2), encoding="utf-8")
    paths.profile_config_path.write_text(json.dumps(_profile_data(config), indent=2), encoding="utf-8")
    paths.credentials_path.write_text(json.dumps(_credentials_data(config), indent=2), encoding="utf-8")
    for target in (paths.config_path, paths.profile_config_path, paths.credentials_path):
        harden_path(target.parent, is_dir=True)
        harden_path(target, is_dir=False)
    return paths.config_path


def set_config_value(config: AppConfig, dotted_key: str, value: str) -> AppConfig:
    data = asdict(config)
    cursor: dict[str, Any] = data
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        if part not in cursor or not isinstance(cursor[part], dict):
            raise KeyError(f"Unknown config section: {part}")
        cursor = cursor[part]
    leaf = parts[-1]
    if leaf not in cursor:
        raise KeyError(f"Unknown config key: {dotted_key}")
    cursor[leaf] = _coerce_config_value(cursor[leaf], value, leaf)
    return _from_data(data)


def redact_config(config: AppConfig) -> dict[str, Any]:
    return _redact(asdict(config))


def _load_legacy_config(path: Path, *, home: str | Path | None, profile: str | None) -> AppConfig:
    cfg = AppConfig.default(home, profile)
    if not path.exists():
        return cfg
    raw = _read_json(path)
    default = asdict(cfg)
    return _from_data(_deep_merge(default, raw))


def _from_parts(paths: AppPaths, profile_raw: dict[str, Any], credentials: dict[str, Any]) -> AppConfig:
    default = asdict(AppConfig.default(paths.roots.config.parent if paths.roots.portable else None, paths.profile))
    # Path values are derived from the selected profile and cannot be overridden
    # by profile JSON. This prevents stale absolute paths from reappearing.
    merged = _deep_merge(default, profile_raw)
    for key in ("profile", "data_dir", "db_path", "media_dir", "state_dir", "cache_dir", "exports_dir", "wiki_dir", "credentials_path"):
        merged[key] = default[key]
    merged["telegram"] = _deep_merge(merged["telegram"], credentials.get("telegram", {}))
    merged["llm"] = _deep_merge(merged["llm"], credentials.get("llm", {}))
    return _from_data(merged)


def _profile_data(config: AppConfig) -> dict[str, Any]:
    return {
        "provider_policy": asdict(config.provider_policy),
        "ai_access": asdict(config.ai_access),
        "llm": {"provider": config.llm.provider, "model": config.llm.model},
        "semantic": asdict(config.semantic),
    }


def _credentials_data(config: AppConfig) -> dict[str, Any]:
    return {
        "telegram": {
            "api_id": config.telegram.api_id,
            "api_hash": config.telegram.api_hash,
            "phone": config.telegram.phone,
        },
        "llm": {"api_key": config.llm.api_key},
    }


def _from_data(data: dict[str, Any]) -> AppConfig:
    return AppConfig(
        profile=data["profile"],
        data_dir=data["data_dir"],
        db_path=data["db_path"],
        media_dir=data["media_dir"],
        state_dir=data["state_dir"],
        cache_dir=data["cache_dir"],
        exports_dir=data["exports_dir"],
        wiki_dir=data["wiki_dir"],
        credentials_path=data["credentials_path"],
        telegram=TelegramConfig(**data["telegram"]),
        provider_policy=ProviderPolicy(**data["provider_policy"]),
        ai_access=AIAccessPolicy(**data["ai_access"]),
        llm=LLMConfig(**data["llm"]),
        semantic=SemanticConfig(**data["semantic"]),
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def _coerce_config_value(current: Any, value: str, leaf: str) -> Any:
    if isinstance(current, bool):
        return value.lower() in {"1", "true", "yes", "on"}
    if isinstance(current, list):
        return [int(item.strip()) for item in value.split(",") if item.strip()]
    if isinstance(current, int) or leaf == "api_id":
        return int(value)
    return value


def _redact(value: Any, key: str | None = None) -> Any:
    if key in SENSITIVE_KEYS and value not in (None, ""):
        return "***REDACTED***"
    if isinstance(value, dict):
        return {k: _redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
