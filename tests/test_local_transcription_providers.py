from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tg_recall.transcription import MAX_TRANSCRIPTION_ERROR_CHARS, FasterWhisperXXLProvider, WhisperCLIProvider


def _completed() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, "", "")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _work_output_dir(argv: list[str]) -> Path:
    return Path(argv[argv.index("--output_dir") + 1])


def test_whisper_default_constructor_keeps_legacy_argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    media = tmp_path / "voice.ogg"
    media.touch()
    output_dir = tmp_path / "output"
    calls: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(
        "tg_recall.transcription.shutil.which", lambda name: "C:/Tools/whisper.exe" if name == "whisper" else None
    )

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        _write_json(_work_output_dir(argv) / "voice.json", {"text": " hello ", "language": "ru", "segments": []})
        return _completed()

    monkeypatch.setattr("tg_recall.transcription.subprocess.run", fake_run)

    result = WhisperCLIProvider(output_dir).transcribe(media)

    assert result.text == "hello"
    assert result.language == "ru"
    assert result.segments == []
    argv, kwargs = calls[0]
    assert argv[:2] == ["C:/Tools/whisper.exe", str(media)]
    assert argv[2:4] == ["--output_dir", str(_work_output_dir(argv))]
    assert _work_output_dir(argv).parent == output_dir
    assert argv[4:] == ["--output_format", "json"]
    assert kwargs == {"capture_output": True, "text": True, "check": False}


def test_whisper_explicit_typed_options_and_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    media = tmp_path / "voice.ogg"
    media.touch()
    output_dir = tmp_path / "output"
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        _write_json(_work_output_dir(argv) / "voice.json", {"text": "ok", "segments": []})
        return _completed()

    monkeypatch.setattr("tg_recall.transcription.subprocess.run", fake_run)

    WhisperCLIProvider(
        output_dir,
        executable="C:/Tools/whisper.exe",
        model="medium",
        language="ru",
        device="cuda:1",
        compute_type="int8_float16",
        vad_filter=False,
        timeout_seconds=120,
    ).transcribe(media)

    argv, kwargs = calls[0]
    assert argv[:2] == [str(Path("C:/Tools/whisper.exe")), str(media)]
    assert _work_output_dir(argv).parent == output_dir
    assert argv[4:] == [
        "--output_format",
        "json",
        "--model",
        "medium",
        "--language",
        "ru",
        "--device",
        "cuda:1",
        "--compute_type",
        "int8_float16",
        "--vad_filter",
        "false",
    ]
    assert kwargs == {"capture_output": True, "text": True, "check": False, "timeout": 120}


def test_faster_whisper_xxl_uses_one_argv_element_for_paths_with_spaces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    installation = tmp_path / "Faster Whisper XXL"
    installation.mkdir()
    executable = installation / "faster-whisper-xxl.exe"
    executable.touch()
    model_root = installation / "_models"
    (model_root / "faster-whisper-large-v3-turbo").mkdir(parents=True)
    media = tmp_path / "Telegram voice.ogg"
    media.touch()
    output_dir = tmp_path / "transcription output"
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        _write_json(
            _work_output_dir(argv) / "Telegram voice.json",
            {"text": "ready", "language": None, "segments": [{"id": 0}]},
        )
        return _completed()

    monkeypatch.setattr("tg_recall.transcription.subprocess.run", fake_run)

    result = FasterWhisperXXLProvider(
        output_dir,
        executable=executable,
        model="large-v3-turbo",
        language="ru",
        device="cuda",
        compute_type="int8_float16",
        vad_filter=False,
        timeout_seconds=60,
    ).transcribe(media)

    assert result.provider == "local-faster-whisper-xxl"
    assert result.text == "ready"
    argv, kwargs = calls[0]
    assert argv[:4] == [
        str(executable.resolve()),
        str(media),
        "--model",
        "large-v3-turbo",
    ]
    assert argv[4:6] == ["--model_dir", str(model_root.resolve())]
    assert argv[6:8] == ["--output_dir", str(_work_output_dir(argv))]
    assert _work_output_dir(argv).parent == output_dir
    assert argv[8:] == [
        "--output_format",
        "json",
        "--language",
        "ru",
        "--device",
        "cuda",
        "--compute_type",
        "int8_float16",
        "--vad_filter",
        "false",
    ]
    assert kwargs == {"capture_output": True, "text": True, "check": False, "timeout": 60}


def test_faster_whisper_xxl_requires_exact_existing_local_model_before_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "faster-whisper-xxl.exe"
    executable.touch()
    called = False

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return _completed()

    monkeypatch.setattr("tg_recall.transcription.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="not available locally"):
        FasterWhisperXXLProvider(tmp_path / "output", executable=executable, model="medium")

    assert not called


@pytest.mark.parametrize("model", ["../medium", "medium; whoami", "medium --device cuda", ""])
def test_faster_whisper_xxl_rejects_unsafe_model_before_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, model: str
) -> None:
    executable = tmp_path / "faster-whisper-xxl.exe"
    executable.touch()
    called = False

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return _completed()

    monkeypatch.setattr("tg_recall.transcription.subprocess.run", fake_run)

    with pytest.raises(ValueError, match="model"):
        FasterWhisperXXLProvider(tmp_path / "output", executable=executable, model=model)

    assert not called


def test_local_provider_accepts_single_spaced_language_name(tmp_path: Path) -> None:
    provider = WhisperCLIProvider(tmp_path / "output", executable="C:/Tools/whisper.exe", language="Haitian Creole")

    assert provider.language == "Haitian Creole"


@pytest.mark.parametrize("language", [" Haitian", "Haitian ", "Haitian  Creole", "ru2", "ru; whoami"])
def test_local_provider_rejects_unsafe_or_malformed_language_name(tmp_path: Path, language: str) -> None:
    with pytest.raises(ValueError, match="language"):
        WhisperCLIProvider(tmp_path / "output", executable="C:/Tools/whisper.exe", language=language)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"segments": []},
        {"text": "", "segments": []},
        {"text": "ok", "language": 42, "segments": []},
        {"text": "ok"},
        {"text": "ok", "segments": {}},
        {"text": "ok", "segments": [{}, "not a dict"]},
    ],
)
def test_local_cli_rejects_incompatible_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: object) -> None:
    media = tmp_path / "voice.ogg"
    media.touch()
    output_dir = tmp_path / "output"

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        _write_json(_work_output_dir(argv) / "voice.json", payload)
        return _completed()

    monkeypatch.setattr("tg_recall.transcription.shutil.which", lambda name: "C:/Tools/whisper.exe")
    monkeypatch.setattr("tg_recall.transcription.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="transcription JSON"):
        WhisperCLIProvider(output_dir).transcribe(media)


@pytest.mark.parametrize("contents", ["", "not json", '{"'])
def test_local_cli_rejects_missing_empty_or_malformed_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, contents: str
) -> None:
    media = tmp_path / "voice.ogg"
    media.touch()
    output_dir = tmp_path / "output"

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if contents:
            work_output_dir = _work_output_dir(argv)
            work_output_dir.mkdir(parents=True, exist_ok=True)
            (work_output_dir / "voice.json").write_text(contents, encoding="utf-8")
        return _completed()

    monkeypatch.setattr("tg_recall.transcription.shutil.which", lambda name: "C:/Tools/whisper.exe")
    monkeypatch.setattr("tg_recall.transcription.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="JSON transcript|valid JSON"):
        WhisperCLIProvider(output_dir).transcribe(media)


def test_local_cli_never_reads_stale_json_from_shared_output_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    media = tmp_path / "voice.ogg"
    media.touch()
    output_dir = tmp_path / "output"
    _write_json(output_dir / "voice.json", {"text": "stale", "segments": []})

    monkeypatch.setattr("tg_recall.transcription.shutil.which", lambda name: "C:/Tools/whisper.exe")
    monkeypatch.setattr("tg_recall.transcription.subprocess.run", lambda *args, **kwargs: _completed())

    with pytest.raises(RuntimeError, match="did not produce a JSON transcript"):
        WhisperCLIProvider(output_dir).transcribe(media)


def test_local_cli_turns_timeout_into_retryable_provider_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    media = tmp_path / "voice.ogg"
    media.touch()

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr("tg_recall.transcription.shutil.which", lambda name: "C:/Tools/whisper.exe")
    monkeypatch.setattr("tg_recall.transcription.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="whisper timed out"):
        WhisperCLIProvider(tmp_path / "output", timeout_seconds=10).transcribe(media)


def test_local_cli_bounds_process_error_before_retry_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    media = tmp_path / "voice.ogg"
    media.touch()
    long_error = "x" * (MAX_TRANSCRIPTION_ERROR_CHARS + 1)

    monkeypatch.setattr("tg_recall.transcription.shutil.which", lambda name: "C:/Tools/whisper.exe")
    monkeypatch.setattr(
        "tg_recall.transcription.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 1, "", long_error),
    )

    with pytest.raises(RuntimeError) as exc_info:
        WhisperCLIProvider(tmp_path / "output").transcribe(media)

    assert str(exc_info.value) == long_error[:MAX_TRANSCRIPTION_ERROR_CHARS]
