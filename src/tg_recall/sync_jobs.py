"""Background Telegram downloads for the long-lived MCP server.

An MCP ``sync`` call waits a little; a longer download keeps running in a
thread of the server process, so the agent is never blocked for minutes and
never has to loop "partial → sync again". One job runs at a time because the
Telegram session is single-user. Interrupting a job is safe: sync is
gap-free and resumable.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .config import AppConfig
from .storage import Database

PROGRESS_SECONDS = 2.0
# After a job ends, a bare sync() still reports it instead of starting a new download.
RECENT_SECONDS = 600.0


@dataclass
class SyncJob:
    targets: list[tuple[int, int | None]]
    since: datetime | None
    started: float = field(default_factory=time.monotonic)
    # Latest progress per (chat_id, topic_id): fetched, date, total.
    progress: dict[tuple[int, int | None], dict[str, Any]] = field(default_factory=dict)
    results: list[dict[str, Any]] | None = None
    error: str | None = None
    reported: bool = False
    finished: float | None = None
    done: threading.Event = field(default_factory=threading.Event)

    def update(self, state: dict[str, Any]) -> None:
        key = (state["chat_id"], state["topic_id"])
        current = self.progress.setdefault(key, {})
        if current.get("first_date") is None and state.get("date") is not None:
            current["first_date"], current["first_at"] = state["date"], time.monotonic()
        current.update(state)

    @property
    def recent(self) -> bool:
        return self.finished is not None and time.monotonic() - self.finished < RECENT_SECONDS

    @property
    def running(self) -> bool:
        return not self.done.is_set()

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def eta_seconds(self, key: tuple[int, int | None]) -> float | None:
        """Linear estimate: time per day of history covered so far × days left.

        New messages arrive oldest-first (towards now); older history newest-first
        (towards ``since``).
        """

        state = self.progress.get(key)
        if not state or not state.get("date") or not state.get("first_date"):
            return None
        first, current = _aware(state["first_date"]), _aware(state["date"])
        if current >= first:
            covered, left = (current - first).total_seconds(), (datetime.now(UTC) - current).total_seconds()
        elif self.since is not None:
            covered, left = (first - current).total_seconds(), (current - self.since).total_seconds()
        else:
            return None
        if covered <= 0 or left <= 0:
            return None
        return (time.monotonic() - state["first_at"]) * left / covered


class SyncJobs:
    """The process-wide background job (one Telegram session, one job)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.job: SyncJob | None = None

    def current(self) -> SyncJob | None:
        return self.job

    def running(self) -> SyncJob | None:
        job = self.job
        return job if job is not None and job.running else None

    def start(self, config: AppConfig, db: Database, targets: list[tuple[int, int | None]], *, since: datetime | None, seconds: float) -> SyncJob:
        with self._lock:
            if self.job is not None and self.job.running:
                return self.job
            job = SyncJob(targets=list(targets), since=since)
            self.job = job
        thread = threading.Thread(target=self._run, args=(job, config, db, seconds), name="tg-recall-sync", daemon=True)
        thread.start()
        return job

    @staticmethod
    def _run(job: SyncJob, config: AppConfig, db: Database, seconds: float) -> None:
        from .telegram_client import TelegramArchiveClient

        try:
            client = TelegramArchiveClient(config, db)
            job.results = asyncio.run(
                client.sync_many(job.targets, since=job.since, max_seconds=seconds, progress=job.update, progress_seconds=PROGRESS_SECONDS)
            )
        except Exception as exc:
            job.error = str(exc).split(";")[0][:200]
        finally:
            job.finished = time.monotonic()
            job.done.set()


def _aware(value: Any) -> datetime:
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)

