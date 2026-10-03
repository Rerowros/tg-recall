from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .models import MediaRecord
from .storage import Database


MAX_TRANSCRIPTION_TIMEOUT_SECONDS = 3_600
MAX_TRANSCRIPTION_ERROR_CHARS = 2_000
SUPPORTED_COMPUTE_TYPES = frozenset(
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
_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_LANGUAGE_RE = re.compile(r"^(?=.{1,32}$)[A-Za-z]+(?: [A-Za-z]+)*$")
_DEVICE_RE = re.compile(r"^(?:auto|cpu|cuda(?::[0-9]+)?)$")


@dataclass(frozen=True)
class TranscriptResult:
    provider: str
    text: str
    language: str | None = None
    segments: list[dict] | None = None
    status: str = "success"
    error_type: str | None = None
    retryable: bool = False


class SpeechToTextProvider(Protocol):
    name: str

    def transcribe(self, media_path: Path) -> TranscriptResult: ...


class DisabledProvider:
    name = "disabled"

    def transcribe(self, media_path: Path) -> TranscriptResult:
        raise RuntimeError("External transcription is disabled by provider policy")


class SidecarTextProvider:
    name = "sidecar-text"

    def transcribe(self, media_path: Path) -> TranscriptResult:
        if media_path.suffix.lower() == ".txt":
            return TranscriptResult(provider=self.name, text=media_path.read_text(encoding="utf-8"))
        sidecar = media_path.with_suffix(media_path.suffix + ".txt")
        if not sidecar.exists():
            raise FileNotFoundError(f"No sidecar transcript found: {sidecar}")
        return TranscriptResult(provider=self.name, text=sidecar.read_text(encoding="utf-8"))


class WhisperCLIProvider:
    name = "local-whisper"

    def __init__(
        self,
        output_dir: str | Path,
        *,
        executable: str | Path | None = None,
        model: str | None = None,
        language: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
        vad_filter: bool | None = None,
        timeout_seconds: int | None = None,
    ):
        self.output_dir = Path(output_dir)
        self.executable = str(executable) if executable is not None else None
        self.model = _validate_model_name(model) if model is not None else None
        self.language = _validate_language(language) if language is not None else None
        self.device = _validate_device(device) if device is not None else None
        self.compute_type = _validate_compute_type(compute_type) if compute_type is not None else None
        self.vad_filter = _validate_vad_filter(vad_filter)
        self.timeout_seconds = _validate_timeout(timeout_seconds)

    @staticmethod
    def available() -> bool:
        return shutil.which("whisper") is not None

    def ready(self) -> bool:
        """The configured executable (or ``whisper`` on PATH) exists."""

        return shutil.which(self.executable) is not None if self.executable else self.available()

    def transcribe(self, media_path: Path) -> TranscriptResult:
        executable = _resolve_executable(self.executable, "whisper")
        if not executable:
            raise RuntimeError("Local Whisper CLI is not installed or not in PATH")
        argv = [executable, str(media_path), "--output_dir", str(self.output_dir), "--output_format", "json"]
        argv.extend(
            _common_cli_options(
                model=self.model,
                language=self.language,
                device=self.device,
                compute_type=self.compute_type,
                vad_filter=self.vad_filter,
            )
        )
        return _run_cli_transcription(
            argv,
            output_dir=self.output_dir,
            media_path=media_path,
            provider_name=self.name,
            failure_label="whisper",
            timeout_seconds=self.timeout_seconds,
        )


class FasterWhisperXXLProvider:
    """Adapter for a preinstalled Faster-Whisper-XXL executable and model."""

    name = "local-faster-whisper-xxl"

    def __init__(
        self,
        output_dir: str | Path,
        *,
        model: str,
        executable: str | Path | None = None,
        model_dir: str | Path | None = None,
        language: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
        vad_filter: bool | None = None,
        timeout_seconds: int | None = None,
    ):
        self.output_dir = Path(output_dir)
        self.model = _validate_model_name(model)
        self.executable = _resolve_local_executable(executable, "faster-whisper-xxl")
        self.model_dir = _resolve_local_model_dir(model_dir, self.executable, self.model)
        self.language = _validate_language(language) if language is not None else None
        self.device = _validate_device(device) if device is not None else None
        self.compute_type = _validate_compute_type(compute_type) if compute_type is not None else None
        self.vad_filter = _validate_vad_filter(vad_filter)
        self.timeout_seconds = _validate_timeout(timeout_seconds)

    @staticmethod
    def available() -> bool:
        return shutil.which("faster-whisper-xxl") is not None

    def ready(self) -> bool:
        # The constructor already resolved the executable and the local model.
        return True

    def transcribe(self, media_path: Path) -> TranscriptResult:
        argv = [
            str(self.executable),
            str(media_path),
            "--model",
            self.model,
            "--model_dir",
            str(self.model_dir),
            "--output_dir",
            str(self.output_dir),
            "--output_format",
            "json",
        ]
        argv.extend(
            _common_cli_options(
                language=self.language,
                device=self.device,
                compute_type=self.compute_type,
                vad_filter=self.vad_filter,
            )
        )
        return _run_cli_transcription(
            argv,
            output_dir=self.output_dir,
            media_path=media_path,
            provider_name=self.name,
            failure_label="faster-whisper-xxl",
            timeout_seconds=self.timeout_seconds,
        )


def _validate_model_name(value: str) -> str:
    if not isinstance(value, str) or not _MODEL_NAME_RE.fullmatch(value):
        raise ValueError("model must contain only letters, digits, dots, underscores, and hyphens")
    return value


def _validate_language(value: str) -> str:
    if not isinstance(value, str) or not _LANGUAGE_RE.fullmatch(value):
        raise ValueError("language must be a supported language code or name")
    return value


def _validate_device(value: str) -> str:
    if not isinstance(value, str) or not _DEVICE_RE.fullmatch(value):
        raise ValueError("device must be auto, cpu, cuda, or cuda:<index>")
    return value


def _validate_compute_type(value: str) -> str:
    if value not in SUPPORTED_COMPUTE_TYPES:
        raise ValueError(f"unsupported compute_type: {value!r}")
    return value


def _validate_vad_filter(value: bool | None) -> bool | None:
    if value is not None and not isinstance(value, bool):
        raise ValueError("vad_filter must be a boolean")
    return value


def _validate_timeout(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= MAX_TRANSCRIPTION_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be between 1 and {MAX_TRANSCRIPTION_TIMEOUT_SECONDS}")
    return value


def _resolve_executable(configured: str | None, default_name: str) -> str | None:
    if configured is None:
        return shutil.which(default_name)
    candidate = Path(configured).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    discovered = shutil.which(configured)
    if discovered:
        return discovered
    # The legacy provider historically deferred an unavailable binary error to
    # process launch. Keep that behavior for an explicit path; the Faster
    # adapter adds the stricter existing-local-file requirement below.
    if candidate.is_absolute() or "/" in configured or "\\" in configured:
        return str(candidate)
    return None


def _resolve_local_executable(configured: str | Path | None, default_name: str) -> Path:
    resolved = _resolve_executable(str(configured) if configured is not None else None, default_name)
    if not resolved:
        raise RuntimeError("Faster-Whisper-XXL executable is not installed or not available locally")
    executable = Path(resolved).resolve()
    if not executable.is_file():
        raise RuntimeError("Faster-Whisper-XXL executable is not an existing local file")
    return executable


def _resolve_local_model_dir(model_dir: str | Path | None, executable: Path, model: str) -> Path:
    root = Path(model_dir).expanduser() if model_dir is not None else executable.parent / "_models"
    root = root.resolve()
    model_path = (root / f"faster-whisper-{model}").resolve()
    try:
        model_path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"Faster-Whisper-XXL model is not available locally: faster-whisper-{model}") from exc
    if not root.is_dir() or not model_path.is_dir():
        raise RuntimeError(f"Faster-Whisper-XXL model is not available locally: faster-whisper-{model}")
    return root


def _common_cli_options(
    *,
    model: str | None = None,
    language: str | None = None,
    device: str | None = None,
    compute_type: str | None = None,
    vad_filter: bool | None = None,
) -> list[str]:
    argv: list[str] = []
    if model is not None:
        argv.extend(["--model", model])
    if language is not None:
        argv.extend(["--language", language])
    if device is not None:
        argv.extend(["--device", device])
    if compute_type is not None:
        argv.extend(["--compute_type", compute_type])
    if vad_filter is not None:
        argv.extend(["--vad_filter", str(vad_filter).lower()])
    return argv


def _run_cli_transcription(
    argv: list[str],
    *,
    output_dir: Path,
    media_path: Path,
    provider_name: str,
    failure_label: str,
    timeout_seconds: int | None,
) -> TranscriptResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir, prefix="transcript-") as working_dir:
        work_output_dir = Path(working_dir)
        argv = _with_work_output_dir(argv, work_output_dir)
        run_kwargs: dict[str, object] = {"capture_output": True, "text": True, "check": False}
        if timeout_seconds is not None:
            run_kwargs["timeout"] = timeout_seconds
        try:
            result = subprocess.run(argv, **run_kwargs)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{failure_label} timed out") from exc
        if result.returncode != 0:
            raise RuntimeError(_bounded_process_error(result, failure_label))
        output = _discover_transcript_output(work_output_dir, media_path, failure_label)
        return _normalize_transcript_json(output, provider_name)


def _with_work_output_dir(argv: list[str], work_output_dir: Path) -> list[str]:
    result = list(argv)
    try:
        output_dir_index = result.index("--output_dir") + 1
    except ValueError as exc:
        raise RuntimeError("local transcription command is missing --output_dir") from exc
    if output_dir_index >= len(result):
        raise RuntimeError("local transcription command has invalid --output_dir")
    result[output_dir_index] = str(work_output_dir)
    return result


def _bounded_process_error(result: subprocess.CompletedProcess[str], failure_label: str) -> str:
    message = (result.stderr or result.stdout or f"{failure_label} failed").strip()
    return message[:MAX_TRANSCRIPTION_ERROR_CHARS]


def _discover_transcript_output(output_dir: Path, media_path: Path, failure_label: str) -> Path:
    outputs = [path for path in output_dir.glob("*.json") if path.is_file()]
    if not outputs:
        raise RuntimeError(f"{failure_label} did not produce a JSON transcript")
    return max(outputs, key=lambda item: item.stat().st_mtime)


def _normalize_transcript_json(output: Path, provider_name: str) -> TranscriptResult:
    try:
        payload = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("transcription output is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("transcription JSON must be an object")
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("transcription JSON must contain non-empty string text")
    language = payload.get("language")
    if language is not None and not isinstance(language, str):
        raise RuntimeError("transcription JSON language must be a string or null")
    segments = payload.get("segments")
    if not isinstance(segments, list) or not all(isinstance(segment, dict) for segment in segments):
        raise RuntimeError("transcription JSON segments must be a list of objects")
    return TranscriptResult(provider=provider_name, text=text.strip(), language=language, segments=segments)


def local_transcription_provider(cfg: Any) -> WhisperCLIProvider | FasterWhisperXXLProvider:
    """Build the profile-selected local provider without accepting shell syntax."""

    settings = cfg.transcription
    output_dir = Path(cfg.cache_dir) / "transcription"
    if settings.backend == "whisper-cli":
        return WhisperCLIProvider(
            output_dir,
            executable=settings.executable,
            model=settings.model,
            language=settings.language,
            device=settings.device,
            compute_type=settings.compute_type,
            vad_filter=settings.vad_filter,
            timeout_seconds=settings.timeout_seconds,
        )
    if settings.backend == "faster-whisper-xxl":
        if settings.model is None:
            raise RuntimeError("Faster-Whisper-XXL requires transcription.model")
        return FasterWhisperXXLProvider(
            output_dir,
            executable=settings.executable,
            model=settings.model,
            model_dir=settings.model_dir,
            language=settings.language,
            device=settings.device,
            compute_type=settings.compute_type,
            vad_filter=settings.vad_filter,
            timeout_seconds=settings.timeout_seconds,
        )
    raise RuntimeError("Unsupported configured local transcription backend")


def is_transcribable_media(media_type: str | None) -> bool:
    return media_type in {"voice", "audio", "video"}


class TranscriptionService:
    def __init__(
        self,
        db: Database,
        fallback_provider: SpeechToTextProvider | None = None,
        *,
        media_store: object | None = None,
        cache_dir: str | Path | None = None,
    ):
        self.db = db
        self.fallback_provider = fallback_provider or SidecarTextProvider()
        self.media_store = media_store
        self.cache_dir = Path(cache_dir) if cache_dir else None

    def run_pending(self, limit: int = 20, media_ids: set[int] | None = None) -> dict[str, int]:
        completed = failed = skipped = 0
        for job in self.db.get_pending_jobs("transcription", limit=limit, media_ids=media_ids):
            if job.media_id is None:
                self.db.update_job(job.id, "failed", "transcription job is missing media id", retryable=False)
                failed += 1
                continue
            media = self.db.get_media(job.media_id)
            if media is None:
                self.db.update_job(job.id, "failed", "media record is missing", retryable=False)
                failed += 1
                continue
            if not is_transcribable_media(media.media_type):
                self.db.update_job(job.id, "skipped", "media type is not transcribable", retryable=False)
                skipped += 1
                continue
            media_path = self._media_path(media)
            if media_path is None or not media_path.exists():
                self.db.update_job(job.id, "retry", "media is not downloaded yet", retryable=True)
                skipped += 1
                continue
            try:
                if media.media_type == "video":
                    media_path = extract_video_audio(media_path, self.cache_dir)
                result = self.fallback_provider.transcribe(media_path)
                if not result.text:
                    raise RuntimeError("transcription provider returned empty text")
                self.db.insert_transcript(media.id, result.provider, result.text, result.language, result.segments)
                self.db.update_job(job.id, "done")
                completed += 1
            except Exception as exc:
                self.db.update_job(job.id, "retry", str(exc), retryable=True)
                failed += 1
        self.db.audit("transcription_run", details={"completed": completed, "failed": failed, "skipped": skipped})
        return {"completed": completed, "failed": failed, "skipped": skipped}

    def _media_path(self, media: MediaRecord) -> Path | None:
        if self.media_store is not None:
            return self.media_store.path_for_media(media)
        return Path(media.local_path) if media.local_path else None


def extract_video_audio(video_path: Path, output_dir: Path | None = None) -> Path:
    if video_path.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm", ".avi"}:
        return video_path
    target_dir = output_dir or video_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    output = target_dir / f"{video_path.stem}.audio.wav"
    if output.exists():
        return output
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-acodec", "pcm_s16le", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "ffmpeg failed").strip())
    return output
