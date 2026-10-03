from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from functools import cache
from typing import Any

from .config import AppConfig
from .media import MediaStore, sha256_file
from .models import ChatRecord, MessageRecord
from .storage import Database
from .transcription import is_transcribable_media


LINK_RE = re.compile(r"https?://\S+")
GENERAL_TOPIC = 1
WRITE_BATCH = 200
# Pause between 100-message pages; Telegram tolerates this and FloodWait is still honoured.
SYNC_WAIT_SECONDS = 0.3


class TelegramDependencyError(RuntimeError):
    pass


class TelegramNotAuthorizedError(RuntimeError):
    """The saved Telegram session is missing or revoked; nothing will prompt for a login."""

    def __init__(self) -> None:
        super().__init__("Telegram session is not authorized. Run `tg-recall telegram auth` in an interactive terminal.")


class TelegramBusyError(RuntimeError):
    """Another tg-recall process holds the Telegram session (usually a running sync)."""

    def __init__(self) -> None:
        super().__init__("busy: another tg-recall process is using the Telegram session (a sync is running); try again later")


class TelegramRetryPendingError(RuntimeError):
    """A persisted FloodWait is still active; no Telegram request was made."""

    def __init__(self, chat_id: int, retry_after: str):
        self.chat_id = chat_id
        self.retry_after = retry_after
        super().__init__(f"Telegram sync for chat {chat_id} is deferred until {retry_after}")


def _load_telethon() -> tuple[Any, Any]:
    try:
        from telethon import TelegramClient
        from telethon.errors import FloodWaitError
    except ImportError as exc:
        raise TelegramDependencyError("Telethon is not installed. Run `uv sync`.") from exc
    return TelegramClient, FloodWaitError


@cache
def _noninteractive_client_class(base: type) -> type:
    """Telethon's ``async with client`` calls ``start()``, which prompts for a phone on stdin.

    CLI ``--json``, MCP and agent callers must never block on that prompt, so the
    context manager only connects and refuses to continue without a saved session.
    """

    class NonInteractiveTelegramClient(base):  # type: ignore[misc, valid-type]
        require_authorized = True

        async def __aenter__(self) -> Any:
            self._session_lock = SessionLock(getattr(getattr(self, "session", None), "filename", None))
            self._session_lock.acquire()
            try:
                await self.connect()
                if self.require_authorized and not await self.is_user_authorized():
                    await self.disconnect()
                    raise TelegramNotAuthorizedError()
            except BaseException:
                self._session_lock.release()
                raise
            return self

        async def __aexit__(self, *exc_info: Any) -> Any:
            try:
                return await super().__aexit__(*exc_info)
            finally:
                lock = getattr(self, "_session_lock", None)
                if lock is not None:
                    lock.release()

    return NonInteractiveTelegramClient


class SessionLock:
    """Process-wide exclusive use of one Telethon session file.

    Two processes on one SQLite session corrupt or lock it, so a second
    caller fails fast (MCP reports "busy") instead of waiting. The OS drops
    the lock when a process dies, so it never goes stale.
    """

    def __init__(self, session_file: str | None):
        self.path = Path(f"{session_file}.lock") if session_file else None
        self._handle: Any = None

    def acquire(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise TelegramBusyError() from exc
        self._handle = handle

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()


class TelegramArchiveClient:
    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db

    async def authorize(self) -> None:
        client = self._client()
        client.require_authorized = False
        async with client:
            if not await client.is_user_authorized():
                if not self.config.telegram.phone:
                    raise ValueError("telegram.phone is required for authorization")
                await client.start(phone=self.config.telegram.phone)
            me = await client.get_me()
            if getattr(me, "id", None):
                self.db.set_meta("self_user_id", str(me.id))
            self.db.audit("telegram_authorized", user_id=getattr(me, "id", None), username=getattr(me, "username", None))

    async def check(self) -> dict[str, Any]:
        client = self._client()
        client.require_authorized = False
        async with client:
            authorized = await client.is_user_authorized()
            me = await client.get_me() if authorized else None
            if getattr(me, "id", None):
                # Lets agent output show the owner's own messages as "я".
                self.db.set_meta("self_user_id", str(me.id))
            return {
                "authorized": authorized,
                "user_id": getattr(me, "id", None),
                "username": getattr(me, "username", None),
            }

    async def discover_chats(self, limit: int | None = None) -> list[ChatRecord]:
        client = self._client()
        chats: list[ChatRecord] = []
        async with client:
            async for dialog in client.iter_dialogs(limit=limit):
                record = chat_from_dialog(dialog)
                self.db.upsert_chat(record)
                if getattr(getattr(dialog, "entity", None), "forum", False):
                    # Marks the chat as a forum; topic titles arrive on its next sync.
                    self.db.upsert_forum_topics(record.chat_id, [(GENERAL_TOPIC, "General")])
                chats.append(record)
        self.db.audit("telegram_chats_discovered", details={"count": len(chats)})
        return chats

    async def sync_scope(
        self,
        scope_name: str,
        limit: int = 100,
        backfill: bool = False,
        effective_scope: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1_000:
            raise ValueError("sync limit must be an integer in 1..1000")
        scope = effective_scope or self.db.get_scope(scope_name)
        if not scope:
            raise ValueError(f"Unknown sync scope: {scope_name}")
        if not scope["chat_ids"]:
            raise ValueError(f"Sync scope has no chats: {scope_name}")

        client = self._client()
        _, FloodWaitError = _load_telethon()
        synced = 0
        media_jobs = 0
        async with client:
            for chat_id in scope["chat_ids"]:
                newest_id: int | None = None
                oldest_id: int | None = None
                since = parse_date(scope.get("since"))
                until = parse_date(scope.get("until"))
                state = self.db.get_sync_state(chat_id)
                if state and _retry_is_pending(state.get("retry_after")):
                    raise TelegramRetryPendingError(chat_id, state["retry_after"])
                iterator_args: dict[str, Any] = {"limit": limit}
                if backfill and state and state.get("oldest_message_id"):
                    iterator_args["max_id"] = state["oldest_message_id"]
                elif not backfill and state and state.get("newest_message_id"):
                    iterator_args["min_id"] = state["newest_message_id"]
                forum = self._is_forum(chat_id)
                batch: list[MessageRecord] = []
                try:
                    async for message in client.iter_messages(chat_id, **iterator_args):
                        message_date = ensure_aware(message.date)
                        if until and message_date > until:
                            continue
                        if since and message_date < since:
                            break
                        record = message_from_telethon(chat_id, message, forum=forum)
                        batch.append(record)
                        newest_id = max(newest_id or record.message_id, record.message_id)
                        oldest_id = min(oldest_id or record.message_id, record.message_id)
                        synced += 1
                        if len(batch) >= WRITE_BATCH:
                            media_jobs += self._store_batch(batch, scope["media_policy"], scope["transcription_policy"])
                    media_jobs += self._store_batch(batch, scope["media_policy"], scope["transcription_policy"])
                    # A completed forward page can safely advance its high
                    # watermark. A completed historical page advances only
                    # the low watermark. Both values stay monotonic in the
                    # repository and are independent cursors.
                    self.db.update_sync_state(
                        chat_id,
                        newest_message_id=None if backfill else newest_id,
                        oldest_message_id=oldest_id if backfill else oldest_id,
                        retry_after=None,
                    )
                except FloodWaitError as exc:
                    self._store_batch(batch, scope["media_policy"], scope["transcription_policy"])
                    retry_after = (datetime.now(UTC) + timedelta(seconds=exc.seconds)).isoformat()
                    # Telegram yields newest-first. Advancing the forward
                    # high watermark after a partial page could skip unseen
                    # messages below it, so forward retry deliberately
                    # replays idempotent upserts. Historical backfill is
                    # safe to checkpoint at its low watermark because the
                    # next page continues strictly older history.
                    self.db.update_sync_state(
                        chat_id,
                        oldest_message_id=oldest_id if backfill else None,
                        retry_after=retry_after,
                    )
                    self.db.audit("telegram_flood_wait", scope_name, chat_id=chat_id, seconds=exc.seconds)
                    raise
        self.db.audit("telegram_scope_synced", scope_name, messages=synced, media_jobs=media_jobs, backfill=backfill)
        return {"messages": synced, "media_jobs": media_jobs, "backfill": int(backfill)}

    async def download_pending_media(self, limit: int = 20, media_ids: set[int] | None = None) -> dict[str, int]:
        client = self._client()
        async with client:
            async def download(chat_id: int, message_id: int, destination: Any) -> Any:
                message = await client.get_messages(chat_id, ids=message_id)
                if not message or not getattr(message, "media", None):
                    raise FileNotFoundError(f"Telegram media not found for {chat_id}/{message_id}")
                path = await client.download_media(message, file=str(destination.parent))
                if not path:
                    raise FileNotFoundError(f"Telegram download returned no path for {chat_id}/{message_id}")
                return path

            completed = 0
            failed = 0
            skipped = 0
            for job in self.db.get_pending_jobs("media_download", limit=limit, media_ids=media_ids):
                if job.chat_id is None or job.message_id is None or job.media_id is None:
                    self.db.update_job(job.id, "failed", "media job is missing ids", retryable=False)
                    failed += 1
                    continue
                media = self.db.get_media(job.media_id)
                store = MediaStore(self.config.media_dir, Path(self.config.cache_dir) / "downloads")
                current_path = store.path_for_media(media) if media else None
                if media and media.status == "downloaded" and current_path and current_path.exists():
                    self.db.update_job(job.id, "done")
                    skipped += 1
                    continue
                try:
                    downloaded = Path(await download(job.chat_id, job.message_id, store.temporary_path(job.id)))
                    digest = sha256_file(downloaded)
                    existing = self.db.find_media_by_sha256(digest)
                    existing_path = store.path_for_media(existing) if existing else None
                    if existing_path and existing_path.exists():
                        final_path = existing_path
                        if downloaded != final_path:
                            downloaded.unlink(missing_ok=True)
                    else:
                        final_path = store.path_for_sha256(digest, downloaded.suffix)
                        final_path.parent.mkdir(parents=True, exist_ok=True)
                        if downloaded != final_path:
                            shutil.move(str(downloaded), final_path)
                    self.db.update_media_downloaded(
                        job.media_id,
                        storage_key=store.storage_key_for_sha256(digest, downloaded.suffix),
                        sha256=digest,
                        size_bytes=final_path.stat().st_size,
                        enqueue_transcription=_job_needs_transcription(job.payload_json),
                    )
                    self.db.update_job(job.id, "done")
                    completed += 1
                except Exception as exc:
                    self.db.update_job(job.id, "retry", str(exc), retryable=True)
                    failed += 1
            self.db.audit("telegram_media_download_run", details={"completed": completed, "failed": failed, "skipped": skipped})
            return {"completed": completed, "failed": failed, "skipped": skipped}

    async def transcribe_pending_with_telegram(self, limit: int = 20, media_ids: set[int] | None = None) -> dict[str, int]:
        try:
            from telethon import functions
        except ImportError as exc:
            raise TelegramDependencyError("Telethon is not installed. Run `uv sync`.") from exc

        client = self._client()
        completed = 0
        failed = 0
        skipped = 0
        async with client:
            for job in self.db.get_pending_jobs("transcription", limit=limit, media_ids=media_ids):
                if job.media_id is None:
                    self.db.update_job(job.id, "failed", "transcription job is missing media id", retryable=False)
                    failed += 1
                    continue
                media = self.db.get_media(job.media_id)
                if media is None or not is_transcribable_media(media.media_type):
                    self.db.update_job(job.id, "skipped", "media is not transcribable", retryable=False)
                    skipped += 1
                    continue
                try:
                    result = await client(
                        functions.messages.TranscribeAudioRequest(
                            peer=media.chat_id,
                            msg_id=media.message_id,
                        )
                    )
                    text = getattr(result, "text", "") or ""
                    pending = bool(getattr(result, "pending", False))
                    if pending and not text:
                        self.db.update_job(job.id, "retry", "Telegram transcription is pending", retryable=True)
                        skipped += 1
                        continue
                    if not text:
                        self.db.update_job(job.id, "retry", "Telegram returned an empty transcript", retryable=True)
                        failed += 1
                        continue
                    self.db.insert_transcript(
                        media.id,
                        "telegram",
                        text,
                        segments=[{"transcription_id": getattr(result, "transcription_id", None), "pending": pending}],
                    )
                    self.db.update_job(job.id, "done")
                    completed += 1
                except Exception as exc:
                    self.db.update_job(job.id, "retry", str(exc), retryable=True)
                    failed += 1
        self.db.audit("telegram_transcription_run", details={"completed": completed, "failed": failed, "skipped": skipped})
        return {"completed": completed, "failed": failed, "skipped": skipped}

    async def sync_chat(
        self,
        chat_id: int,
        *,
        topic_id: int | None = None,
        since: datetime | None = None,
        max_seconds: float = 60.0,
        max_messages: int = 100_000,
    ) -> dict[str, Any]:
        """Fill one chat (or one forum topic) from ``since`` to now in a single connection.

        Two interruption-safe passes: newer than the newest stored message,
        oldest-first, so a stop never leaves a gap below the watermark; then
        older than the oldest stored message, newest-first, down to ``since``.
        A forum topic is fetched by itself (GetReplies), not the whole group.
        """

        if since is not None:
            since = ensure_aware(since)
        client = self._client()
        _, FloodWaitError = _load_telethon()
        deadline = time.monotonic() + max_seconds
        fetched = 0
        complete = True
        retry_after: str | None = None
        reply_to = topic_id if topic_id not in (None, GENERAL_TOPIC) else None
        async with client:
            forum = await self._refresh_topics(client, chat_id)
            bounds = self.db.message_bounds(chat_id, topic_id)
            passes: list[dict[str, Any]] = []
            if bounds:
                passes.append({"min_id": bounds["newest_id"], "reverse": True})
                oldest_date = ensure_aware(datetime.fromisoformat(bounds["oldest_date"]))
                if since is None or oldest_date > since:
                    passes.append({"max_id": bounds["oldest_id"]})
            else:
                passes.append({})
            try:
                for extra in passes:
                    batch: list[MessageRecord] = []
                    reverse = bool(extra.get("reverse"))
                    async for message in client.iter_messages(chat_id, limit=None, reply_to=reply_to, wait_time=SYNC_WAIT_SECONDS, **extra):
                        if since and not reverse and ensure_aware(message.date) < since:
                            break
                        record = message_from_telethon(chat_id, message, forum=forum)
                        if topic_id == GENERAL_TOPIC and record.topic_id not in (None, GENERAL_TOPIC):
                            continue
                        batch.append(record)
                        fetched += 1
                        if len(batch) >= WRITE_BATCH:
                            self._store_batch(batch, "none", "off")
                        if fetched >= max_messages or time.monotonic() > deadline:
                            complete = False
                            break
                    self._store_batch(batch, "none", "off")
                    if not complete:
                        break
            except FloodWaitError as exc:
                self._store_batch(batch, "none", "off")
                complete = False
                retry_after = (datetime.now(UTC) + timedelta(seconds=exc.seconds)).isoformat()
        self.db.update_sync_state(chat_id, retry_after=None)
        self.db.audit("telegram_chat_synced", str(chat_id), chat_id=chat_id, topic_id=topic_id, messages=fetched, complete=complete)
        after = self.db.message_bounds(chat_id, topic_id)
        return {
            "chat_id": chat_id,
            "topic_id": topic_id,
            "forum": forum,
            "fetched": fetched,
            "complete": complete,
            "retry_after": retry_after,
            "stored": after["count"] if after else 0,
            "oldest_date": after["oldest_date"] if after else None,
            "newest_date": after["newest_date"] if after else None,
        }

    async def _refresh_topics(self, client: Any, chat_id: int) -> bool:
        """Store forum topic titles; returns whether the chat is a forum."""

        try:
            entity = await client.get_entity(chat_id)
        except Exception:
            return self._is_forum(chat_id)
        if not getattr(entity, "forum", False):
            return False
        topics: list[tuple[int, str]] = [(GENERAL_TOPIC, "General")]
        try:
            from telethon.tl.functions.messages import GetForumTopicsRequest

            result = await client(GetForumTopicsRequest(peer=entity, offset_date=None, offset_id=0, offset_topic=0, limit=100))
            topics += [(int(topic.id), str(topic.title)) for topic in getattr(result, "topics", []) if getattr(topic, "title", None)]
        except Exception:
            # Titles are a convenience; message topic ids still work without them.
            pass
        self.db.upsert_forum_topics(chat_id, topics)
        return True

    def _is_forum(self, chat_id: int) -> bool:
        return bool(self.db.forum_topics([chat_id]).get(chat_id))

    def _store_batch(self, batch: list[MessageRecord], media_policy: str, transcription_policy: str) -> int:
        if not batch:
            return 0
        self.db.upsert_messages(batch)
        media_jobs = 0
        for record in batch:
            if record.has_media and record.media_type and media_policy_allows(media_policy, record.media_type):
                media_id = self.db.enqueue_media(
                    record.chat_id,
                    record.message_id,
                    record.media_type,
                    telegram_file_id=str(record.message_id),
                    transcription_policy=transcription_policy,
                )
                if transcription_policy != "off":
                    media = self.db.get_media(media_id)
                    if media and media.status == "downloaded":
                        self.db.enqueue_transcription(media_id)
                media_jobs += 1
        batch.clear()
        return media_jobs

    def _client(self) -> Any:
        tg = self.config.telegram
        if not tg.api_id or not tg.api_hash:
            raise ValueError("telegram.api_id and telegram.api_hash are required")
        TelegramClient, _ = _load_telethon()
        return _noninteractive_client_class(TelegramClient)(tg.session_path, tg.api_id, tg.api_hash)


def run_async(coro: Any) -> Any:
    return asyncio.run(coro)


def chat_from_dialog(dialog: Any) -> ChatRecord:
    entity = getattr(dialog, "entity", None)
    username = getattr(entity, "username", None)
    is_user = bool(getattr(dialog, "is_user", False))
    is_group = bool(getattr(dialog, "is_group", False))
    is_channel = bool(getattr(dialog, "is_channel", False))
    if is_user:
        chat_type = "user"
    elif is_group:
        chat_type = "group"
    elif is_channel:
        chat_type = "channel"
    else:
        chat_type = "unknown"
    return ChatRecord(
        chat_id=int(dialog.id),
        title=str(dialog.name or dialog.id),
        chat_type=chat_type,
        username=username,
        is_eligible=chat_type != "unknown",
    )


def message_from_telethon(chat_id: int, message: Any, *, forum: bool = False) -> MessageRecord:
    text = getattr(message, "raw_text", None) or getattr(message, "text", None) or ""
    media_type = detect_media_type(message)
    sender = getattr(message, "sender", None)
    sender_name = None
    if sender is not None:
        sender_name = " ".join(
            part for part in [getattr(sender, "first_name", None), getattr(sender, "last_name", None)] if part
        ) or getattr(sender, "username", None)
    reply_to_id, topic_id = _reply_and_topic(message, forum)
    return MessageRecord(
        chat_id=int(chat_id),
        message_id=int(message.id),
        date=message.date,
        text=text,
        sender_id=getattr(message, "sender_id", None),
        sender_name=sender_name,
        reply_to_message_id=reply_to_id,
        forward_from=forward_label(getattr(message, "fwd_from", None)),
        edit_date=getattr(message, "edit_date", None),
        has_media=media_type is not None,
        media_type=media_type,
        links_json=json.dumps(LINK_RE.findall(text), ensure_ascii=False),
        topic_id=topic_id,
    )


def _reply_and_topic(message: Any, forum: bool) -> tuple[int | None, int | None]:
    """Split Telegram's reply header into a real reply and the forum topic.

    In a forum every message in a topic "replies" to the topic root; that is
    membership, not a reply, so it becomes ``topic_id`` and the reply is kept
    only when it points at another message (``reply_to_top_id`` is set).
    """

    reply = getattr(message, "reply_to", None)
    reply_id = getattr(reply, "reply_to_msg_id", None)
    if type(getattr(message, "action", None)).__name__ == "MessageActionTopicCreate":
        return None, int(message.id)
    if reply is not None and getattr(reply, "forum_topic", False):
        top = getattr(reply, "reply_to_top_id", None)
        if top:
            return reply_id, int(top)
        return None, int(reply_id) if reply_id else None
    if forum:
        return reply_id, GENERAL_TOPIC
    return reply_id, None


def forward_label(fwd: Any) -> str | None:
    """A short, stable origin for a forward instead of Telethon's object repr."""

    if not fwd:
        return None
    name = getattr(fwd, "from_name", None)
    if name:
        return str(name)
    peer = getattr(fwd, "from_id", None)
    for attribute, kind in (("user_id", "user"), ("channel_id", "channel"), ("chat_id", "chat")):
        value = getattr(peer, attribute, None)
        if value:
            return f"{kind}:{value}"
    return "hidden"


def detect_media_type(message: Any) -> str | None:
    if getattr(message, "voice", None):
        return "voice"
    if getattr(message, "audio", None):
        return "audio"
    if getattr(message, "video", None):
        return "video"
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "document", None):
        return "document"
    if getattr(message, "media", None):
        return "media"
    return None


def media_policy_allows(policy: str, media_type: str) -> bool:
    values = {item.strip() for item in policy.split(",") if item.strip()}
    return "all" in values or media_type in values


def _job_needs_transcription(payload_json: str) -> bool:
    try:
        return json.loads(payload_json).get("transcription_policy", "auto") != "off"
    except json.JSONDecodeError:
        return True


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _retry_is_pending(value: str | None) -> bool:
    if not value:
        return False
    try:
        return ensure_aware(datetime.fromisoformat(value.replace("Z", "+00:00"))) > datetime.now(UTC)
    except ValueError:
        # A malformed legacy retry timestamp must not permanently block a
        # bounded user-requested sync. A successful page clears it below.
        return False
