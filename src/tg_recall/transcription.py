from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import MediaRecord
from .storage import Database


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

    def transcribe(self, media_path: Path) -> TranscriptResult:
        ...


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

    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)

    @staticmethod
    def available() -> bool:
        return shutil.which("whisper") is not None

    def transcribe(self, media_path: Path) -> TranscriptResult:
        executable = shutil.which("whisper")
        if not executable:
            raise RuntimeError("Local Whisper CLI is not installed or not in PATH")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        before = set(self.output_dir.glob("*.json"))
        result = subprocess.run(
            [executable, str(media_path), "--output_dir", str(self.output_dir), "--output_format", "json"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "whisper failed").strip())
        outputs = [path for path in self.output_dir.glob("*.json") if path not in before]
        if not outputs:
            expected = self.output_dir / f"{media_path.stem}.json"
            outputs = [expected] if expected.exists() else []
        if not outputs:
            raise RuntimeError("whisper did not produce a JSON transcript")
        payload = json.loads(max(outputs, key=lambda item: item.stat().st_mtime).read_text(encoding="utf-8"))
        return TranscriptResult(
            provider=self.name,
            text=str(payload.get("text", "")).strip(),
            language=payload.get("language"),
            segments=payload.get("segments") or [],
        )


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
