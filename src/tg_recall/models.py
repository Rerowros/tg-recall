from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


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
    # Forum topic root id (1 = General); None outside forum chats.
    topic_id: int | None = None


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
    media_types: tuple[str, ...] | None = None
    has_link: bool | None = None
    chat_ids: tuple[int, ...] | None = None
    topic_id: int | None = None

    def normalized(self) -> "SearchFilters":
        if self.chat_id is not None and (isinstance(self.chat_id, bool) or not isinstance(self.chat_id, int)):
            raise ValueError("chat_id filter must be an integer")
        chat_ids = None
        if self.chat_ids is not None:
            if any(isinstance(value, bool) or not isinstance(value, int) for value in self.chat_ids):
                raise ValueError("chat_ids filter must contain integers")
            chat_ids = tuple(sorted(set(self.chat_ids)))
            if self.chat_id is not None:
                # Both set: the single chat must lie inside the set, otherwise nothing matches.
                chat_ids = (self.chat_id,) if self.chat_id in chat_ids else ()
        if self.sender_id is not None and (isinstance(self.sender_id, bool) or not isinstance(self.sender_id, int)):
            raise ValueError("sender_id filter must be an integer")
        for value in (self.since, self.until):
            if value is not None:
                _parse_filter_date(value)
        if self.since and self.until and self.since > self.until:
            raise ValueError("since filter must not be after until")
        allowed_media = {"voice", "audio", "photo", "video", "document", "media"}
        if self.media_type is not None and self.media_type not in allowed_media:
            raise ValueError("unknown media_type filter")
        values = tuple(sorted(set(self.media_types))) if self.media_types is not None else None
        if values is not None and not set(values) <= allowed_media:
            raise ValueError("unknown media_types filter")
        if self.media_type is not None and values is not None:
            raise ValueError("media_type and media_types filters cannot be combined")
        if self.has_link is not None and not isinstance(self.has_link, bool):
            raise ValueError("has_link filter must be boolean")
        return SearchFilters(
            chat_id=self.chat_id,
            sender_id=self.sender_id,
            since=self.since,
            until=self.until,
            media_type=self.media_type,
            media_types=values,
            has_link=self.has_link,
            chat_ids=chat_ids,
            topic_id=self.topic_id,
        )


def _parse_filter_date(value: str) -> None:
    if not isinstance(value, str):
        raise ValueError("date filters must be ISO-8601 strings")
    try:
        if len(value) == 10:
            date.fromisoformat(value)
        else:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("date filters must be ISO-8601 strings") from exc


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
