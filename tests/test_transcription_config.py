from __future__ import annotations

import json

import pytest

from tg_recall.config import AppConfig, TranscriptionConfig, load_config, save_config, set_config_value
from tg_recall.paths import AppPaths


def test_transcription_config_defaults_preserve_legacy_whisper_cli() -> None:
    config = AppConfig.default()

    assert config.transcription == TranscriptionConfig(
        backend="whisper-cli",
        executable=None,
        model=None,
        model_dir=None,
        language=None,
        device=None,
        compute_type=None,
        vad_filter=None,
        timeout_seconds=600,
    )


def test_transcription_config_round_trips_in_profile_not_credentials(tmp_path) -> None:
    home = tmp_path / "portable"
    config = AppConfig.default(home, "work")
    config.transcription = TranscriptionConfig(
        backend="faster-whisper-xxl",
        executable=r"C:\Program Files\Whisper\faster-whisper-xxl.exe",
        model="large-v3-turbo",
        model_dir=r"D:\models",
        language="ru",
        device="cuda",
        compute_type="int8_float16",
        vad_filter=False,
        timeout_seconds=900,
    )

    save_config(config, home=home)
    paths = AppPaths.resolve(home, "work")
    profile = json.loads(paths.profile_config_path.read_text(encoding="utf-8"))
    credentials = json.loads(paths.credentials_path.read_text(encoding="utf-8"))

    assert profile["transcription"] == {
        "backend": "faster-whisper-xxl",
        "executable": r"C:\Program Files\Whisper\faster-whisper-xxl.exe",
        "model": "large-v3-turbo",
        "model_dir": r"D:\models",
        "language": "ru",
        "device": "cuda",
        "compute_type": "int8_float16",
        "vad_filter": False,
        "timeout_seconds": 900,
    }
    assert "transcription" not in credentials
    assert load_config(home=home, profile="work").transcription == config.transcription


def test_transcription_config_set_coerces_every_supported_value() -> None:
    config = AppConfig.default()
    settings = {
        "transcription.backend": "faster-whisper-xxl",
        "transcription.executable": r"C:\Program Files\Whisper\faster-whisper-xxl.exe",
        "transcription.model": "large-v3-turbo",
        "transcription.model_dir": r"D:\models",
        "transcription.language": "ru",
        "transcription.device": "cuda",
        "transcription.compute_type": "int8_float16",
        "transcription.vad_filter": "false",
        "transcription.timeout_seconds": "900",
    }

    for key, value in settings.items():
        config = set_config_value(config, key, value)

    assert config.transcription == TranscriptionConfig(
        backend="faster-whisper-xxl",
        executable=r"C:\Program Files\Whisper\faster-whisper-xxl.exe",
        model="large-v3-turbo",
        model_dir=r"D:\models",
        language="ru",
        device="cuda",
        compute_type="int8_float16",
        vad_filter=False,
        timeout_seconds=900,
    )
    for key in (
        "transcription.executable",
        "transcription.model",
        "transcription.model_dir",
        "transcription.language",
        "transcription.device",
        "transcription.compute_type",
        "transcription.vad_filter",
    ):
        config = set_config_value(config, key, "null")

    assert config.transcription.executable is None
    assert config.transcription.model is None
    assert config.transcription.model_dir is None
    assert config.transcription.language is None
    assert config.transcription.device is None
    assert config.transcription.compute_type is None
    assert config.transcription.vad_filter is None


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"backend": "shell-template"}, "backend"),
        ({"model": "large-v3;--download"}, "model"),
        ({"language": "ru;--task"}, "language"),
        ({"device": "gpu0"}, "device"),
        ({"compute_type": "fp8"}, "compute type"),
        ({"vad_filter": "false"}, "vad_filter"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"timeout_seconds": 3_601}, "timeout_seconds"),
        ({"timeout_seconds": True}, "timeout_seconds"),
        ({"executable": ""}, "executable"),
        ({"model_dir": "\x00"}, "model_dir"),
    ],
)
def test_transcription_config_rejects_invalid_values(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        TranscriptionConfig(**kwargs)


def test_transcription_config_rejects_extra_args_and_invalid_config_set_boolean() -> None:
    with pytest.raises(TypeError, match="extra_args"):
        TranscriptionConfig(**{"extra_args": ["--download-model"]})
    with pytest.raises(ValueError, match="vad_filter"):
        set_config_value(AppConfig.default(), "transcription.vad_filter", "sometimes")


@pytest.mark.parametrize(
    "compute_type",
    [
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
    ],
)
def test_transcription_config_accepts_faster_whisper_xxl_compute_types(compute_type) -> None:
    assert TranscriptionConfig(compute_type=compute_type).compute_type == compute_type


@pytest.mark.parametrize("device", ["auto", "cpu", "cuda", "cuda:0", "cuda:12", "cuda:999"])
def test_transcription_config_accepts_faster_whisper_xxl_devices(device) -> None:
    assert TranscriptionConfig(device=device).device == device


def test_transcription_config_accepts_multi_word_language_name() -> None:
    assert TranscriptionConfig(language="Haitian Creole").language == "Haitian Creole"


@pytest.mark.parametrize("language", [" Haitian", "Haitian ", "Haitian  Creole", "Haitian2", "ru;--task"])
def test_transcription_config_rejects_unsafe_language_names(language) -> None:
    with pytest.raises(ValueError, match="language"):
        TranscriptionConfig(language=language)


@pytest.mark.parametrize("device", ["gpu", "cuda:-1", "cuda:1x", "cuda:1000", "cuda;--help"])
def test_transcription_config_rejects_unsafe_faster_whisper_xxl_devices(device) -> None:
    with pytest.raises(ValueError, match="device"):
        TranscriptionConfig(device=device)


def test_legacy_profile_without_transcription_section_uses_defaults(tmp_path) -> None:
    home = tmp_path / "portable"
    config = AppConfig.default(home, "work")
    save_config(config, home=home)
    paths = AppPaths.resolve(home, "work")
    profile = json.loads(paths.profile_config_path.read_text(encoding="utf-8"))
    del profile["transcription"]
    paths.profile_config_path.write_text(json.dumps(profile), encoding="utf-8")

    loaded = load_config(home=home, profile="work")

    assert loaded.transcription == TranscriptionConfig()
