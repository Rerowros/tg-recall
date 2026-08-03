from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Callable

from .models import MediaRecord
from .storage import Database
from .transcription import is_transcribable_media


class MediaStore:
    def __init__(self, root: str | Path, cache_root: str | Path | None = None):
        self.root = Path(root)
        self.cache_root = Path(cache_root) if cache_root else self.root / "_tmp"
        self.root.mkdir(parents=True, exist_ok=True)
        self.cache_root.mkdir(parents=True, exist_ok=True)

    def storage_key_for_sha256(self, digest: str, suffix: str = "") -> str:
        clean_suffix = suffix if suffix.startswith(".") or suffix == "" else f".{suffix}"
        return f"sha256/{digest[:2]}/{digest[2:4]}/{digest}{clean_suffix}"

    def path_for_sha256(self, digest: str, suffix: str = "") -> Path:
        return self.path_for_storage_key(self.storage_key_for_sha256(digest, suffix))

    def path_for_storage_key(self, storage_key: str) -> Path:
        candidate = (self.root / storage_key).resolve()
        root = self.root.resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("media storage key escapes the object store")
        return candidate

    def path_for_media(self, media: MediaRecord) -> Path | None:
        if media.storage_key:
            return self.path_for_storage_key(media.storage_key)
        return Path(media.local_path) if media.local_path else None

    def temporary_path(self, job_id: int, suffix: str = ".download") -> Path:
        return self.cache_root / str(job_id) / f"download{suffix}"

    def disk_usage_bytes(self) -> int:
        if not self.root.exists():
            return 0
        return sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


DownloadFunc = Callable[[int, int, Path], Path]


class MediaDownloader:
    def __init__(self, db: Database, store: MediaStore, download_func: DownloadFunc):
        self.db = db
        self.store = store
        self.download_func = download_func

    def run_pending(self, limit: int = 20) -> dict[str, int]:
        completed = failed = skipped = 0
        for job in self.db.get_pending_jobs("media_download", limit=limit):
            if job.chat_id is None or job.message_id is None or job.media_id is None:
                self.db.update_job(job.id, "failed", "media job is missing chat/message/media id", retryable=False)
                failed += 1
                continue
            media = self.db.get_media(job.media_id)
            current_path = self.store.path_for_media(media) if media else None
            if media and media.status == "downloaded" and current_path and current_path.exists():
                if is_transcribable_media(media.media_type) and _needs_transcription(job.payload_json):
                    self.db.enqueue_transcription(media.id)
                self.db.update_job(job.id, "done")
                skipped += 1
                continue
            try:
                downloaded = self.download_func(job.chat_id, job.message_id, self.store.temporary_path(job.id))
                digest = sha256_file(downloaded)
                suffix = Path(downloaded).suffix
                existing = self.db.find_media_by_sha256(digest)
                existing_path = self.store.path_for_media(existing) if existing else None
                if existing_path and existing_path.exists():
                    final_path = existing_path
                    if Path(downloaded).exists() and Path(downloaded) != final_path:
                        Path(downloaded).unlink(missing_ok=True)
                else:
                    final_path = self.store.path_for_sha256(digest, suffix=suffix)
                    final_path.parent.mkdir(parents=True, exist_ok=True)
                    if Path(downloaded) != final_path:
                        shutil.move(str(downloaded), final_path)
                self.db.update_media_downloaded(
                    media.id,
                    storage_key=self.store.storage_key_for_sha256(digest, suffix),
                    sha256=digest,
                    size_bytes=final_path.stat().st_size,
                    enqueue_transcription=is_transcribable_media(media.media_type) and _needs_transcription(job.payload_json),
                )
                self.db.update_job(job.id, "done")
                completed += 1
            except Exception as exc:
                self.db.update_job(job.id, "retry", str(exc), retryable=True)
                self.db.mark_media_failed(job.media_id, str(exc))
                failed += 1
        self.db.audit("media_download_run", details={"completed": completed, "failed": failed, "skipped": skipped})
        return {"completed": completed, "failed": failed, "skipped": skipped}


def _needs_transcription(payload_json: str) -> bool:
    try:
        policy = json.loads(payload_json).get("transcription_policy", "auto")
    except json.JSONDecodeError:
        policy = "auto"
    return policy != "off"


def copy_file_download(source: str | Path) -> DownloadFunc:
    source_path = Path(source)

    def _download(chat_id: int, message_id: int, destination: Path) -> Path:
        target = destination.with_suffix(source_path.suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)
        return target

    return _download
