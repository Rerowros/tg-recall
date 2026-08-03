from __future__ import annotations

import asyncio
import json
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import AppConfig
from .media import MediaStore, sha256_file
from .models import ChatRecord, MessageRecord
from .storage import Database
from .transcription import is_transcribable_media


LINK_RE = re.compile(r"https?://\S+")


class TelegramDependencyError(RuntimeError):
    pass


def _load_telethon() -> tuple[Any, Any]:
    try:
        from telethon import TelegramClient
        from telethon.errors import FloodWaitError
    except ImportError as exc:
        raise TelegramDependencyError("Telethon is not installed. Run `uv sync`.") from exc
    return TelegramClient, FloodWaitError


class TelegramArchiveClient:
    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db

    async def authorize(self) -> None:
        client = self._client()
        async with client:
            if not await client.is_user_authorized():
                if not self.config.telegram.phone:
                    raise ValueError("telegram.phone is required for authorization")
                await client.start(phone=self.config.telegram.phone)
            me = await client.get_me()
            self.db.audit("telegram_authorized", user_id=getattr(me, "id", None), username=getattr(me, "username", None))

    async def check(self) -> dict[str, Any]:
        client = self._client()
        async with client:
            authorized = await client.is_user_authorized()
            me = await client.get_me() if authorized else None
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
                chats.append(record)
        self.db.audit("telegram_chats_discovered", details={"count": len(chats)})
        return chats

    async def sync_scope(self, scope_name: str, limit: int = 100, backfill: bool = False) -> dict[str, int]:
        scope = self.db.get_scope(scope_name)
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
                iterator_args: dict[str, Any] = {"limit": limit}
                if backfill and state and state.get("oldest_message_id"):
                    iterator_args["max_id"] = state["oldest_message_id"]
                elif not backfill and state and state.get("newest_message_id"):
                    iterator_args["min_id"] = state["newest_message_id"]
                try:
                    async for message in client.iter_messages(chat_id, **iterator_args):
                        message_date = ensure_aware(message.date)
                        if until and message_date > until:
                            continue
                        if since and message_date < since:
                            break
                        record = message_from_telethon(chat_id, message)
                        self.db.upsert_message(record)
                        newest_id = max(newest_id or record.message_id, record.message_id)
                        oldest_id = min(oldest_id or record.message_id, record.message_id)
                        synced += 1
                        if record.has_media and record.media_type and media_policy_allows(scope["media_policy"], record.media_type):
                            media_id = self.db.enqueue_media(
                                chat_id,
                                record.message_id,
                                record.media_type,
                                telegram_file_id=str(record.message_id),
                                transcription_policy=scope["transcription_policy"],
                            )
                            if scope["transcription_policy"] != "off":
                                media = self.db.get_media(media_id)
                                if media and media.status == "downloaded":
                                    self.db.enqueue_transcription(media_id)
                            media_jobs += 1
                    self.db.update_sync_state(chat_id, newest_message_id=newest_id, oldest_message_id=oldest_id)
                except FloodWaitError as exc:
                    retry_after = (datetime.now(UTC) + timedelta(seconds=exc.seconds)).isoformat()
                    self.db.update_sync_state(chat_id, retry_after=retry_after)
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

    def _client(self) -> Any:
        tg = self.config.telegram
        if not tg.api_id or not tg.api_hash:
            raise ValueError("telegram.api_id and telegram.api_hash are required")
        TelegramClient, _ = _load_telethon()
        return TelegramClient(tg.session_path, tg.api_id, tg.api_hash)


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


def message_from_telethon(chat_id: int, message: Any) -> MessageRecord:
    text = getattr(message, "raw_text", None) or getattr(message, "text", None) or ""
    media_type = detect_media_type(message)
    sender = getattr(message, "sender", None)
    sender_name = None
    if sender is not None:
        sender_name = " ".join(
            part for part in [getattr(sender, "first_name", None), getattr(sender, "last_name", None)] if part
        ) or getattr(sender, "username", None)
    return MessageRecord(
        chat_id=int(chat_id),
        message_id=int(message.id),
        date=message.date,
        text=text,
        sender_id=getattr(message, "sender_id", None),
        sender_name=sender_name,
        reply_to_message_id=getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None),
        forward_from=str(getattr(message, "fwd_from", "")) if getattr(message, "fwd_from", None) else None,
        edit_date=getattr(message, "edit_date", None),
        has_media=media_type is not None,
        media_type=media_type,
        links_json=json.dumps(LINK_RE.findall(text), ensure_ascii=False),
    )


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
