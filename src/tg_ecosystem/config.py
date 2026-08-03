from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .security import harden_path


SENSITIVE_KEYS = {"api_hash", "phone", "session_path", "openai_api_key", "provider_key", "api_key"}


def default_state_dir() -> Path:
    return Path(os.environ.get("TG_ECOSYSTEM_HOME", ".tg-ecosystem")).resolve()


@dataclass
class TelegramConfig:
    api_id: int | None = None
    api_hash: str | None = None
    phone: str | None = None
    session_path: str = ".tg-ecosystem/telegram.session"


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
    data_dir: str = ".tg-ecosystem"
    db_path: str = ".tg-ecosystem/archive.sqlite3"
    media_dir: str = ".tg-ecosystem/media"
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    provider_policy: ProviderPolicy = field(default_factory=ProviderPolicy)
    ai_access: AIAccessPolicy = field(default_factory=AIAccessPolicy)
    llm: LLMConfig = field(default_factory=LLMConfig)
    semantic: SemanticConfig = field(default_factory=SemanticConfig)

    @classmethod
    def default(cls) -> "AppConfig":
        state_dir = default_state_dir()
        return cls(
            data_dir=str(state_dir),
            db_path=str(state_dir / "archive.sqlite3"),
            media_dir=str(state_dir / "media"),
            telegram=TelegramConfig(session_path=str(state_dir / "telegram.session")),
        )

    def ensure_dirs(self) -> None:
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        Path(self.media_dir).mkdir(parents=True, exist_ok=True)
        Path(self.telegram.session_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        harden_path(self.data_dir, is_dir=True)
        harden_path(self.media_dir, is_dir=True)
        harden_path(Path(self.telegram.session_path).parent, is_dir=True)
        harden_path(Path(self.db_path).parent, is_dir=True)


def config_path(path: str | Path | None = None) -> Path:
    return Path(path).resolve() if path else default_state_dir() / "config.json"


def load_config(path: str | Path | None = None) -> AppConfig:
    cfg_path = config_path(path)
    if not cfg_path.exists():
        cfg = AppConfig.default()
        return cfg

    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    default = asdict(AppConfig.default())
    merged = _deep_merge(default, raw)
    return AppConfig(
        data_dir=merged["data_dir"],
        db_path=merged["db_path"],
        media_dir=merged["media_dir"],
        telegram=TelegramConfig(**merged["telegram"]),
        provider_policy=ProviderPolicy(**merged["provider_policy"]),
        ai_access=AIAccessPolicy(**merged["ai_access"]),
        llm=LLMConfig(**merged["llm"]),
        semantic=SemanticConfig(**merged["semantic"]),
    )


def save_config(config: AppConfig, path: str | Path | None = None) -> Path:
    cfg_path = config_path(path)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
    harden_path(cfg_path.parent, is_dir=True)
    harden_path(cfg_path, is_dir=False)
    return cfg_path


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

    current = cursor[leaf]
    if isinstance(current, bool):
        cursor[leaf] = value.lower() in {"1", "true", "yes", "on"}
    elif isinstance(current, list):
        cursor[leaf] = [int(item.strip()) for item in value.split(",") if item.strip()]
    elif isinstance(current, int) or leaf == "api_id":
        cursor[leaf] = int(value)
    else:
        cursor[leaf] = value

    return AppConfig(
        data_dir=data["data_dir"],
        db_path=data["db_path"],
        media_dir=data["media_dir"],
        telegram=TelegramConfig(**data["telegram"]),
        provider_policy=ProviderPolicy(**data["provider_policy"]),
        ai_access=AIAccessPolicy(**data["ai_access"]),
        llm=LLMConfig(**data["llm"]),
        semantic=SemanticConfig(**data["semantic"]),
    )


def redact_config(config: AppConfig) -> dict[str, Any]:
    return _redact(asdict(config))


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
