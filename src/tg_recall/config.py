from __future__ import annotations

import json
import os
import re
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .paths import AppPaths
from .security import harden_path


SENSITIVE_KEYS = {
    "access_token",
    "api_hash",
    "api_key",
    "client_secret",
    "credentials_path",
    "openai_api_key",
    "phone",
    "provider_key",
    "provider_secret",
    "secret",
    "session_path",
}


_TRANSCRIPTION_BACKENDS = frozenset({"whisper-cli", "faster-whisper-xxl"})
_TRANSCRIPTION_COMPUTE_TYPES = frozenset(
    {
        "default",
        "auto",
        "int8",
        "int8_float16",
        "int8_float32",
        "int8_bfloat16",
        "int16",
        "float16",
        "float32",
        "bfloat16",
    }
)
_SAFE_TRANSCRIPTION_DEVICE = re.compile(r"(?:auto|cpu|cuda(?::[0-9]{1,3})?)\Z")
_SAFE_TRANSCRIPTION_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_TRANSCRIPTION_LANGUAGE = re.compile(r"[A-Za-z]+(?: [A-Za-z]+)*\Z")
_MAX_TRANSCRIPTION_TIMEOUT_SECONDS = 3_600
OPENROUTER_EMBEDDING_MODELS = {
    "perplexity/pplx-embed-v1-0.6b": {"label": "Perplexity Embed 0.6B", "usd_per_million_input_tokens": 0.004},
    "perplexity/pplx-embed-v1-4b": {"label": "Perplexity Embed 4B", "usd_per_million_input_tokens": 0.03},
    "voyageai/voyage-4-lite": {"label": "Voyage 4 Lite", "usd_per_million_input_tokens": 0.02},
}


@dataclass
class TelegramConfig:
    api_id: int | None = None
    api_hash: str | None = None
    phone: str | None = None
    session_path: str = ""


@dataclass
class ProviderPolicy:
    external_llm_enabled: bool = False
    # An empty list is a deliberate default-deny boundary. Only these two
    # declared fields may be serialized in an external LLM request.
    external_llm_data_classes: list[str] = field(default_factory=list)
    external_transcription_enabled: bool = False
    external_embeddings_enabled: bool = False


@dataclass
class AIAccessPolicy:
    enabled: bool = False
    allowed_chat_ids: list[int] = field(default_factory=list)
    max_results: int = 20
    # Optional policy boundaries are deliberately permissive by default to
    # preserve existing v0.2 configurations. Once configured, automation sees
    # only their intersection with a command or saved sync scope.
    allowed_since: str | None = None
    allowed_until: str | None = None
    allowed_media_types: str = "all"
    # Messages one `read` call may return (search hits stay capped by max_results).
    max_read_messages: int = 200
    # Expose the resumable research-session tools over MCP (most agents never need them).
    mcp_research_tools: bool = False
    # Put allowed chat titles into MCP initialize instructions (costs tokens in every session).
    instructions_list_chats: bool = False


@dataclass
class LLMConfig:
    """Provider identity plus one private credential loaded outside profile JSON.

    The default is intentionally local/extractive.  ``api_key`` is written
    only by the normal profile save path into that profile's private
    credentials file; it is never part of profile configuration or exposed
    unredacted by config output. Explicit legacy-path saves retain their historical,
    caller-owned monolithic format for compatibility.
    """

    provider: str = "extractive"
    model: str | None = None
    api_key: str | None = None


@dataclass
class SemanticConfig:
    enabled: bool = False
    provider: str = "local-token"
    # `sentence-transformers-local` is an opt-in path to files the user has
    # already downloaded.  It is never interpreted as a hub model identifier.
    model_path: str | None = None
    model: str | None = None
    device: str = "cpu"
    batch_size: int = 32
    request_timeout_seconds: int = 20


@dataclass
class TranscriptionConfig:
    """Typed, profile-local settings for the selected local transcription CLI."""

    backend: str = "whisper-cli"
    executable: str | None = None
    model: str | None = None
    model_dir: str | None = None
    language: str | None = None
    device: str | None = None
    compute_type: str | None = None
    vad_filter: bool | None = None
    timeout_seconds: int = 600

    def __post_init__(self) -> None:
        if self.backend not in _TRANSCRIPTION_BACKENDS:
            raise ValueError(f"Unsupported transcription backend: {self.backend}")
        for field_name in ("executable", "model_dir"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value or "\x00" in value):
                raise ValueError(f"transcription.{field_name} must be a non-empty string or null")
        if self.model is not None and (
            not isinstance(self.model, str) or not _SAFE_TRANSCRIPTION_MODEL.fullmatch(self.model)
        ):
            raise ValueError("transcription.model must contain only letters, digits, dots, underscores, and hyphens")
        if self.language is not None and (
            not isinstance(self.language, str)
            or len(self.language) > 32
            or not _SAFE_TRANSCRIPTION_LANGUAGE.fullmatch(self.language)
        ):
            raise ValueError("transcription.language must be a language code or name with letters and single spaces")
        if self.device is not None and (
            not isinstance(self.device, str) or not _SAFE_TRANSCRIPTION_DEVICE.fullmatch(self.device)
        ):
            raise ValueError(f"Unsupported transcription device: {self.device}")
        if self.compute_type is not None and self.compute_type not in _TRANSCRIPTION_COMPUTE_TYPES:
            raise ValueError(f"Unsupported transcription compute type: {self.compute_type}")
        if self.vad_filter is not None and not isinstance(self.vad_filter, bool):
            raise ValueError("transcription.vad_filter must be a boolean or null")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or not 1 <= self.timeout_seconds <= _MAX_TRANSCRIPTION_TIMEOUT_SECONDS
        ):
            raise ValueError(
                f"transcription.timeout_seconds must be between 1 and {_MAX_TRANSCRIPTION_TIMEOUT_SECONDS}"
            )


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
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)

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
    selected_profile = (
        profile or os.environ.get("TG_RECALL_PROFILE") or global_raw.get("active_profile") or paths.profile
    )
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
    for key in (
        "profile",
        "data_dir",
        "db_path",
        "media_dir",
        "state_dir",
        "cache_dir",
        "exports_dir",
        "wiki_dir",
        "credentials_path",
    ):
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
        "transcription": asdict(config.transcription),
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
        transcription=TranscriptionConfig(**data.get("transcription", {})),
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def _coerce_config_value(current: Any, value: str, leaf: str) -> Any:
    if value.lower() in {"none", "null"}:
        if current is None or leaf in {
            "executable",
            "model",
            "model_dir",
            "language",
            "device",
            "compute_type",
            "vad_filter",
        }:
            return None
    if isinstance(current, bool):
        return _coerce_bool(value, leaf)
    if leaf == "vad_filter":
        return _coerce_bool(value, leaf)
    if isinstance(current, list):
        if leaf.endswith("_data_classes"):
            values = [item.strip() for item in value.split(",") if item.strip()]
            allowed = {"message_text", "metadata"}
            if len(values) != len(set(values)) or set(values) != allowed:
                raise ValueError("external_llm_data_classes must be exactly message_text,metadata")
            return sorted(values)
        return [int(item.strip()) for item in value.split(",") if item.strip()]
    if isinstance(current, int) or leaf == "api_id":
        return int(value)
    return value


def _coerce_bool(value: str, leaf: str) -> bool:
    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{leaf} must be a boolean")


def _redact(value: Any, key: str | None = None) -> Any:
    if isinstance(key, str) and key.casefold() in SENSITIVE_KEYS and value not in (None, ""):
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
