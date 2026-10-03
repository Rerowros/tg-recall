"""Compact, line-oriented text for AI agents.

One message is always exactly one line that starts with a marker and the
message id, so text inside a message cannot fake a hit, a chat header or an
author: newlines are folded and control characters dropped.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Iterable, Sequence

from .agent_query import AgentMessage, ChatInfo
from .context_budgeting import estimate_text_tokens

HIT_CHARS = 600
CONTEXT_CHARS = 200
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f  ]")
_NEWLINE_RE = re.compile(r"\s*(?:\r\n|\r|\n)\s*")
_FWD_NAME_RE = re.compile(r"from_name='((?:[^'\\]|\\.)*)'")
_FWD_PEER_RE = re.compile(r"from_id=Peer(User|Channel|Chat)\((?:user|channel|chat)_id=(-?\d+)\)")
_MEDIA_LABELS = {"voice": "voice", "audio": "audio", "photo": "photo", "video": "video", "document": "doc", "media": "media"}


@dataclass
class RenderContext:
    titles: dict[int, str]
    self_id: int | None = None
    full: bool = False
    topics: dict[int, dict[int, str]] = field(default_factory=dict)
    now: datetime = field(default_factory=lambda: datetime.now(UTC))


def tz_label(now: datetime | None = None) -> str:
    offset = (now or datetime.now(UTC)).astimezone().utcoffset()
    minutes = int(offset.total_seconds() // 60) if offset else 0
    sign = "+" if minutes >= 0 else "-"
    hours, rest = divmod(abs(minutes), 60)
    return f"{sign}{hours:02d}" + (f":{rest:02d}" if rest else "")


def ago(value: str | None, now: datetime | None = None) -> str:
    if not value:
        return "never"
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    seconds = max(0, int(((now or datetime.now(UTC)) - moment).total_seconds()))
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{seconds // 60}m ago"
    if seconds < 48 * 3600:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def render_messages(
    groups: Sequence[tuple[int, Sequence[AgentMessage | None]]],
    ctx: RenderContext,
) -> str:
    """Render chats in order; ``None`` inside a group marks a gap between windows."""

    lines: list[str] = []
    for chat_id, messages in groups:
        lines.append(f"## {_clean(ctx.titles.get(chat_id) or str(chat_id), 80)} ({chat_id})")
        shown = {message.message_id for message in messages if message is not None}
        targets = Counter(message.reply_to for message in messages if message is not None and message.reply_to)
        last_day: str | None = None
        last_topic: int | None = None
        for message in messages:
            if message is None:
                lines.append("  ⋯")
                continue
            if message.topic_id is not None and message.topic_id != last_topic:
                title = ctx.topics.get(chat_id, {}).get(message.topic_id) or "topic"
                lines.append(f"# {_clean(title, 60)} (/{message.topic_id})")
                last_topic = message.topic_id
                last_day = None
            local = _local(message.date)
            day = local.strftime("%Y-%m-%d") if local.year != ctx.now.astimezone().year else local.strftime("%m-%d")
            if day != last_day:
                lines.append(f"-- {day} --")
                last_day = day
            lines.append(_message_line(message, local, ctx, _show_reply(message, shown, targets)))
    return "\n".join(lines)


def _show_reply(message: AgentMessage, shown: set[int], targets: Counter[int]) -> bool:
    """Keep ↩ when it links inside the output, or points a hit at a specific parent.

    Forum topics make every message a reply to the topic root; a target that
    many shown messages share is that root and only adds noise.
    """

    if not message.reply_to:
        return False
    if message.reply_to in shown:
        return True
    return message.hit and targets[message.reply_to] < 3


def render_chat_list(chats: Sequence[ChatInfo], now: datetime | None = None) -> str:
    if not chats:
        return "0 allowed chats match"
    lines = [f"{len(chats)} allowed chats · id · title · type · msgs · last message · synced"]
    for chat in chats:
        last = _local(chat.last_date).strftime("%Y-%m-%d") if chat.last_date else "-"
        lines.append(
            f"{chat.chat_id} · {_clean(chat.title, 80)} · {chat.chat_type} · {_compact_count(chat.messages)} · {last} · {ago(chat.last_synced_at, now)}"
        )
        if chat.topics:
            shown = ", ".join(f"/{topic_id} {_clean(title, 30)} {_compact_count(count)}" for topic_id, title, count in chat.topics[:10])
            more = f", +{len(chat.topics) - 10}" if len(chat.topics) > 10 else ""
            lines.append(f"  topics: {shown}{more}")
    return "\n".join(lines)


def citations_line(messages: Iterable[AgentMessage]) -> str:
    refs = [message.citation for message in messages]
    return "cite: " + " ".join(refs) if refs else ""


def token_estimate(text: str) -> int:
    return estimate_text_tokens(text)


def forward_label(raw: str | None) -> str | None:
    if not raw:
        return None
    name = _FWD_NAME_RE.search(raw)
    if name:
        return _clean(name.group(1), 40)
    peer = _FWD_PEER_RE.search(raw)
    if peer:
        return {"User": "user", "Channel": "channel", "Chat": "chat"}[peer.group(1)]
    return "?" if raw.startswith("MessageFwdHeader") else _clean(raw, 40)


def _message_line(message: AgentMessage, local: datetime, ctx: RenderContext, show_reply: bool = True) -> str:
    marker = ">" if message.hit else " "
    sender = _sender(message, ctx)
    meta = ""
    if message.reply_to and show_reply:
        meta += f" ↩{message.reply_to}"
    forwarded = forward_label(message.forward_from)
    if forwarded:
        meta += f" fwd:{forwarded}"
    limit = None if ctx.full else (HIT_CHARS if message.hit else CONTEXT_CHARS)
    body = _body(message, limit)
    return f"{marker}{message.message_id} {local.strftime('%H:%M')} {sender}{meta}: {body}"


def _sender(message: AgentMessage, ctx: RenderContext) -> str:
    if ctx.self_id is not None and message.sender_id == ctx.self_id:
        return "я"
    name = _clean(message.sender_name or "", 40).replace(":", "")
    if not name:
        return f"id{message.sender_id}" if message.sender_id else "?"
    if name in {"я", "me"}:
        name = f"{name}#{message.sender_id}"
    return name


def _body(message: AgentMessage, limit: int | None) -> str:
    text = _clean(message.text or "", None)
    if message.media_type:
        label = _MEDIA_LABELS.get(message.media_type, message.media_type)
        if message.transcript:
            transcript = _clean(message.transcript, None)
            media = f"[{label}] «{transcript}»"
        elif message.media_type in {"voice", "audio", "video"}:
            media = f"[{label} · no transcript]"
        else:
            media = f"[{label}]"
        text = f"{media} {text}".strip()
    if limit is not None and len(text) > limit:
        cut = text.rfind(" ", 0, limit)
        cut = cut if cut > limit * 0.6 else limit
        text = f"{text[:cut]}…[+{len(text) - cut}]"
    return text or "(empty)"


def _clean(value: str, limit: int | None) -> str:
    text = _NEWLINE_RE.sub(" ⏎ ", value)
    text = _CONTROL_RE.sub("", text).strip()
    if limit is not None and len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _local(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, UTC).astimezone()
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone()


def _compact_count(value: int) -> str:
    return f"{value / 1000:.1f}k" if value >= 1000 else str(value)
