from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Callable

from .storage import Database
from .transcription import is_transcribable_media

class MediaStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for_sha256(self, digest: str, suffix: str = "") -> Path:
        clean_suffix = suffix if suffix.startswith(".") or suffix == "" else f".{suffix}"
        path = self.root / digest[:2] / digest[2:4] / f"{digest}{clean_suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

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
        completed = 0
        failed = 0
        skipped = 0
        for job in self.db.get_pending_jobs("media_download", limit=limit):
            if job.chat_id is None or job.message_id is None or job.media_id is None:
                self.db.update_job(job.id, "failed", "media job is missing chat/message/media id", retryable=False)
                failed += 1
                continue
            media = self.db.get_media(job.media_id)
            if media is None:
                self.db.update_job(job.id, "failed", "media record is missing", retryable=False)
                failed += 1
                continue
            if media.status == "downloaded" and media.local_path:
                if is_transcribable_media(media.media_type):
                    self.db.enqueue_transcription(media.id)
                self.db.update_job(job.id, "done")
                skipped += 1
                continue
            try:
                temp_path = self.store.root / "_tmp" / f"{job.id}.download"
                temp_path.parent.mkdir(parents=True, exist_ok=True)
                downloaded = self.download_func(job.chat_id, job.message_id, temp_path)
                digest = sha256_file(downloaded)
                suffix = Path(downloaded).suffix
                existing = self.db.find_media_by_sha256(digest)
                if existing and existing.local_path:
                    final_path = Path(existing.local_path)
                    if Path(downloaded).exists() and Path(downloaded) != final_path:
                        Path(downloaded).unlink(missing_ok=True)
                else:
                    final_path = self.store.path_for_sha256(digest, suffix=suffix)
                    if Path(downloaded) != final_path:
                        shutil.move(str(downloaded), final_path)
                self.db.update_media_downloaded(
                    media.id,
                    local_path=str(final_path),
                    sha256=digest,
                    size_bytes=final_path.stat().st_size,
                )
                self.db.update_job(job.id, "done")
                completed += 1
            except Exception as exc:
                self.db.update_job(job.id, "failed", str(exc), retryable=True)
                self.db.mark_media_failed(job.media_id, str(exc))
                failed += 1
        self.db.audit("media_download_run", details={"completed": completed, "failed": failed, "skipped": skipped})
        return {"completed": completed, "failed": failed, "skipped": skipped}


def copy_file_download(source: str | Path) -> DownloadFunc:
    source_path = Path(source)

    def _download(chat_id: int, message_id: int, destination: Path) -> Path:
        suffix = source_path.suffix
        target = destination.with_suffix(suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)
        return target

    return _download
