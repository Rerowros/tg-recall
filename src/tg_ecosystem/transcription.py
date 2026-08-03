from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

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


def is_transcribable_media(media_type: str | None) -> bool:
    return media_type in {"voice", "audio", "video"}


class TelegramTranscriptionProvider:
    name = "telegram"

    def transcribe_media_record(self, media_id: int) -> TranscriptResult:
        return TranscriptResult(
            provider=self.name,
            text="",
            status="unavailable",
            error_type="telegram_transcription_not_connected",
            retryable=False,
        )


class SidecarTextProvider:
    name = "sidecar-text"

    def transcribe(self, media_path: Path) -> TranscriptResult:
        if media_path.suffix.lower() == ".txt":
            return TranscriptResult(provider=self.name, text=media_path.read_text(encoding="utf-8"))
        sidecar = media_path.with_suffix(media_path.suffix + ".txt")
        if not sidecar.exists():
            raise FileNotFoundError(f"No sidecar transcript found: {sidecar}")
        return TranscriptResult(provider=self.name, text=sidecar.read_text(encoding="utf-8"))


class TranscriptionService:
    def __init__(
        self,
        db: Database,
        fallback_provider: SpeechToTextProvider | None = None,
        telegram_provider: TelegramTranscriptionProvider | None = None,
    ):
        self.db = db
        self.fallback_provider = fallback_provider or SidecarTextProvider()
        self.telegram_provider = telegram_provider or TelegramTranscriptionProvider()

    def run_pending(self, limit: int = 20) -> dict[str, int]:
        completed = 0
        failed = 0
        skipped = 0
        for job in self.db.get_pending_jobs("transcription", limit=limit):
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
            if not media.local_path:
                self.db.update_job(job.id, "retry", "media is not downloaded yet", retryable=True)
                skipped += 1
                continue

            telegram_result = self.telegram_provider.transcribe_media_record(media.id)
            if telegram_result.status == "success" and telegram_result.text:
                self.db.insert_transcript(media.id, telegram_result.provider, telegram_result.text, telegram_result.language, telegram_result.segments)
                self.db.update_job(job.id, "done")
                completed += 1
                continue

            try:
                media_path = Path(media.local_path)
                if media.media_type == "video":
                    extracted = extract_video_audio(media_path)
                    media_path = extracted or media_path
                result = self.fallback_provider.transcribe(media_path)
                self.db.insert_transcript(media.id, result.provider, result.text, result.language, result.segments)
                self.db.update_job(job.id, "done")
                completed += 1
            except Exception as exc:
                self.db.update_job(job.id, "failed", str(exc), retryable=True)
                failed += 1
        self.db.audit("transcription_run", details={"completed": completed, "failed": failed, "skipped": skipped})
        return {"completed": completed, "failed": failed, "skipped": skipped}


def extract_video_audio(video_path: Path) -> Path | None:
    if video_path.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm", ".avi"}:
        return None
    output = video_path.with_suffix(video_path.suffix + ".audio.wav")
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
