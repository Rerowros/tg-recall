from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ChatRecord:
    chat_id: int
    title: str
    chat_type: str
    username: str | None = None
    is_eligible: bool = True


@dataclass(frozen=True)
class MessageRecord:
    chat_id: int
    message_id: int
    date: datetime
    text: str
    sender_id: int | None = None
    sender_name: str | None = None
    reply_to_message_id: int | None = None
    forward_from: str | None = None
    edit_date: datetime | None = None
    has_media: bool = False
    media_type: str | None = None
    links_json: str = "[]"


@dataclass(frozen=True)
class SearchResult:
    chat_id: int
    chat_title: str
    message_id: int
    timestamp: str
    text: str
    media_id: int | None = None
    transcript_id: int | None = None
    rank: float | None = None

    @property
    def citation(self) -> str:
        return f"tg://chat/{self.chat_id}/message/{self.message_id}"


@dataclass(frozen=True)
class SearchFilters:
    chat_id: int | None = None
    sender_id: int | None = None
    since: str | None = None
    until: str | None = None
    media_type: str | None = None
    has_link: bool | None = None


@dataclass(frozen=True)
class JobRecord:
    id: int
    stage: str
    status: str
    chat_id: int | None = None
    message_id: int | None = None
    media_id: int | None = None
    payload_json: str = "{}"


@dataclass(frozen=True)
class MediaRecord:
    id: int
    chat_id: int
    message_id: int
    media_type: str
    telegram_file_id: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    storage_key: str | None = None
    local_path: str | None = None
    status: str = "pending"
