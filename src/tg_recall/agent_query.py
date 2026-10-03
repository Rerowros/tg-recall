"""Archive queries shaped for AI agents: multi-chat, sender-aware, context windows.

Every function takes an explicit tuple of permitted chat ids; callers resolve
that tuple through the AI access policy first and nothing here widens it.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Iterable, Sequence

from .storage import Database

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_RELATIVE_RE = re.compile(r"^(\d+)\s*([mhdw])$")
_CITATION_RE = re.compile(r"^(?:tg://chat/)?(-?\d+)(?:/message/|/)(\d+)$")
_STOPWORDS = frozenset(
    """
    и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от
    меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж
    вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без
    будто чего раз тоже себе под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один
    почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при наконец два об другой хоть после
    над больше тот через эти нас про всего них какая много разве три эту моя впрочем хорошо свою этой перед
    иногда лучше чуть том нельзя такой им более всегда конечно всю между это
    the a an and or of to in on for is are was were be been it this that with as at by from about what who
    when where which how did do does we you they he she i me my our your
    """.split()
)


@dataclass(frozen=True)
class AgentMessage:
    chat_id: int
    message_id: int
    date: str
    text: str
    sender_id: int | None = None
    sender_name: str | None = None
    reply_to: int | None = None
    forward_from: str | None = None
    media_type: str | None = None
    transcript: str | None = None
    hit: bool = False

    @property
    def citation(self) -> str:
        return f"tg://chat/{self.chat_id}/message/{self.message_id}"


@dataclass(frozen=True)
class ChatInfo:
    chat_id: int
    title: str
    chat_type: str
    messages: int
    last_date: str | None
    last_synced_at: str | None


@dataclass(frozen=True)
class Scope:
    """The already-authorised slice of the archive a query may touch."""

    chat_ids: tuple[int, ...]
    since: str | None = None
    until: str | None = None
    media_types: tuple[str, ...] | None = None  # explicit filter; () = any media; None = no filter
    allowed_media: tuple[str, ...] | None = None  # policy restriction on media rows; None = all
    sender_ids: tuple[int, ...] | None = None
    sender_name: str | None = None


class AgentQueryError(ValueError):
    """A caller mistake the agent can fix (unknown chat, bad date, bad ref)."""


# ---------------------------------------------------------------- dates


def parse_when(value: str | None, *, end: bool = False, now: datetime | None = None) -> str | None:
    """Turn ``7d``/``24h``/``today``/``yesterday``/ISO into a UTC ISO bound.

    Bare dates are local calendar days; as an ``until`` bound they cover the
    whole day.
    """

    if value is None or str(value).strip() == "":
        return None
    raw = str(value).strip().lower()
    current = (now or datetime.now(UTC)).astimezone()
    if raw in {"today", "сегодня"}:
        return _local_day_bound(current.date(), end=end)
    if raw in {"yesterday", "вчера"}:
        return _local_day_bound(current.date() - timedelta(days=1), end=end)
    match = _RELATIVE_RE.match(raw)
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        delta = {"m": timedelta(minutes=amount), "h": timedelta(hours=amount), "d": timedelta(days=amount), "w": timedelta(weeks=amount)}[unit]
        return (current - delta).astimezone(UTC).isoformat()
    try:
        if len(raw) == 10:
            return _local_day_bound(date.fromisoformat(raw), end=end)
        parsed = datetime.fromisoformat(raw.replace("z", "+00:00"))
    except ValueError as exc:
        raise AgentQueryError(f"bad date {value!r}: use ISO (2026-09-30), 7d, 24h, today or yesterday") from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(UTC).isoformat()


def _local_day_bound(day: date, *, end: bool) -> str:
    local_tz = datetime.now().astimezone().tzinfo
    moment = datetime.combine(day + timedelta(days=1) if end else day, time.min, tzinfo=local_tz)
    if end:
        moment -= timedelta(microseconds=1)
    return moment.astimezone(UTC).isoformat()


# ---------------------------------------------------------------- chats


def resolve_chats(db: Database, allowed: Sequence[int], spec: Any) -> tuple[int, ...]:
    """Resolve ids or title fragments against the allowlist only.

    A fragment that matches several allowed chats selects all of them, which
    lets "BPN" cover every BPN chat in one call.
    """

    allowed_set = tuple(dict.fromkeys(int(value) for value in allowed))
    if spec is None or spec == "" or spec == []:
        return allowed_set
    items = spec if isinstance(spec, list) else [spec]
    titles = _titles(db, allowed_set)
    chosen: list[int] = []
    for item in items:
        if isinstance(item, bool):
            raise AgentQueryError("chats: expected chat id or title fragment")
        if isinstance(item, int) or (isinstance(item, str) and re.fullmatch(r"-?\d+", item.strip())):
            chat_id = int(item)
            if chat_id not in allowed_set:
                raise AgentQueryError(f"chat_not_allowed: {chat_id} is not in the AI allowlist")
            chosen.append(chat_id)
            continue
        if not isinstance(item, str):
            raise AgentQueryError("chats: expected chat id or title fragment")
        needle = item.strip().casefold()
        matches = [chat_id for chat_id in allowed_set if needle and needle in titles.get(chat_id, "").casefold()]
        if not matches:
            known = ", ".join(f"{titles.get(chat_id) or chat_id}" for chat_id in allowed_set[:12])
            raise AgentQueryError(f"chats: no allowed chat matches {item!r}; allowed: {known}")
        chosen.extend(matches)
    return tuple(dict.fromkeys(chosen))


def list_chats(db: Database, chat_ids: Sequence[int], query: str | None = None) -> list[ChatInfo]:
    if not chat_ids:
        return []
    marks = ", ".join("?" for _ in chat_ids)
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT c.chat_id, c.title, c.chat_type,
                   (SELECT COUNT(*) FROM messages m WHERE m.chat_id = c.chat_id) AS messages,
                   (SELECT MAX(m.date) FROM messages m WHERE m.chat_id = c.chat_id) AS last_date,
                   s.last_synced_at
            FROM chats c
            LEFT JOIN sync_state s ON s.chat_id = c.chat_id
            WHERE c.chat_id IN ({marks})
            """,
            list(chat_ids),
        ).fetchall()
    found = {row["chat_id"]: row for row in rows}
    result = []
    for chat_id in chat_ids:
        row = found.get(chat_id)
        info = ChatInfo(
            chat_id=chat_id,
            title=row["title"] if row else "",
            chat_type=row["chat_type"] if row else "unknown",
            messages=row["messages"] if row else 0,
            last_date=row["last_date"] if row else None,
            last_synced_at=row["last_synced_at"] if row else None,
        )
        if query and query.casefold() not in info.title.casefold():
            continue
        result.append(info)
    result.sort(key=lambda item: item.last_date or "", reverse=True)
    return result


def chat_titles(db: Database, chat_ids: Iterable[int]) -> dict[int, str]:
    return _titles(db, tuple(chat_ids))


def _titles(db: Database, chat_ids: tuple[int, ...]) -> dict[int, str]:
    if not chat_ids:
        return {}
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT chat_id, title FROM chats WHERE chat_id IN ({', '.join('?' for _ in chat_ids)})", list(chat_ids)
        ).fetchall()
    return {row["chat_id"]: row["title"] for row in rows}


def archive_synced_at(db: Database, chat_ids: Sequence[int]) -> str | None:
    if not chat_ids:
        return None
    with db.connect() as conn:
        row = conn.execute(
            f"SELECT MAX(last_synced_at) AS synced FROM sync_state WHERE chat_id IN ({', '.join('?' for _ in chat_ids)})",
            list(chat_ids),
        ).fetchone()
    return row["synced"] if row else None


# ---------------------------------------------------------------- self / senders


def self_user_id(db: Database) -> int | None:
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM agent_meta WHERE key = 'self_user_id'").fetchone()
        if row and row["value"]:
            return int(row["value"])
        audit = conn.execute(
            "SELECT details_json FROM audit_events WHERE event_type = 'telegram_authorized' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if audit:
        user_id = json.loads(audit["details_json"]).get("user_id")
        return int(user_id) if user_id is not None else None
    return None


def resolve_sender(db: Database, spec: Any) -> tuple[tuple[int, ...] | None, str | None]:
    """Return (sender ids, sender name fragment) for a ``from`` argument."""

    if spec is None or spec == "":
        return None, None
    if isinstance(spec, int) and not isinstance(spec, bool):
        return (spec,), None
    text = str(spec).strip()
    if text.casefold() in {"me", "я", "self"}:
        own = self_user_id(db)
        if own is None:
            raise AgentQueryError("from='me' needs the owner's Telegram id; the owner should run `tg-recall telegram check`")
        return (own,), None
    if re.fullmatch(r"-?\d+", text):
        return (int(text),), None
    return None, text


# ---------------------------------------------------------------- full-text query


def build_fts_queries(query: str) -> tuple[str | None, str | None]:
    """Return (AND query, OR query) safe for FTS5, with light stemming."""

    words = [word.replace("ё", "е") for word in _WORD_RE.findall(query.casefold())]
    meaningful = [word for word in words if word not in _STOPWORDS] or words
    terms = list(dict.fromkeys(_fts_term(word) for word in meaningful if word))
    if not terms:
        return None, None
    and_query = " AND ".join(terms)
    or_query = " OR ".join(terms) if len(terms) > 1 else None
    return and_query, or_query


def _fts_term(word: str) -> str:
    if len(word) >= 7 and not word.isdigit():
        return f'"{word[:-2]}"*'
    if len(word) >= 5 and not word.isdigit():
        return f'"{word[:-1]}"*'
    return f'"{word}"'


# ---------------------------------------------------------------- search


def search_hits(db: Database, scope: Scope, query: str, limit: int) -> list[tuple[int, int]]:
    """Rank (chat_id, message_id) hits: all terms first, then any term."""

    and_query, or_query = build_fts_queries(query)
    if and_query is None:
        raise AgentQueryError("query has no searchable words")
    hits: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    with db.connect() as conn:
        for fts in (and_query, or_query):
            if fts is None or len(hits) >= limit:
                continue
            for key in _fts_hits(conn, scope, fts, limit):
                if key not in seen:
                    seen.add(key)
                    hits.append(key)
                if len(hits) >= limit:
                    break
    return hits


def _fts_hits(conn: sqlite3.Connection, scope: Scope, fts: str, limit: int) -> list[tuple[int, int]]:
    where, params = _scope_sql("m", scope)
    rows = conn.execute(
        f"""
        SELECT m.chat_id, m.message_id, bm25(messages_fts) AS rank, m.date
        FROM messages_fts
        JOIN messages m ON m.id = messages_fts.rowid
        WHERE messages_fts MATCH ?{where}
        ORDER BY rank, m.date DESC
        LIMIT ?
        """,
        [f"text : ({fts})", *params, limit],
    ).fetchall()
    t_where, t_params = _scope_sql("m", scope)
    transcript_rows = conn.execute(
        f"""
        SELECT m.chat_id, m.message_id, bm25(transcripts_fts) AS rank, m.date
        FROM transcripts_fts
        JOIN transcripts t ON t.id = transcripts_fts.rowid
        JOIN media md ON md.id = t.media_id
        JOIN messages m ON m.chat_id = md.chat_id AND m.message_id = md.message_id
        WHERE transcripts_fts MATCH ?{t_where}
        ORDER BY rank, m.date DESC
        LIMIT ?
        """,
        [fts, *t_params, limit],
    ).fetchall()
    merged = sorted([*rows, *transcript_rows], key=lambda row: (row["rank"], -_epoch(row["date"])))
    return [(row["chat_id"], row["message_id"]) for row in merged]


def _epoch(value: str | None) -> float:
    try:
        return datetime.fromisoformat(value).timestamp() if value else 0.0
    except ValueError:
        return 0.0


# ---------------------------------------------------------------- fetching messages


_MESSAGE_COLUMNS = """
    m.chat_id, m.message_id, m.date, m.text, m.sender_id, m.sender_name, m.reply_to_message_id,
    m.forward_from, m.media_type,
    (SELECT t.text FROM media md JOIN transcripts t ON t.media_id = md.id
       WHERE md.chat_id = m.chat_id AND md.message_id = m.message_id AND t.status = 'success'
       ORDER BY t.id DESC LIMIT 1) AS transcript
"""


def fetch_messages(db: Database, scope: Scope, keys: Sequence[tuple[int, int]]) -> dict[tuple[int, int], AgentMessage]:
    if not keys:
        return {}
    result: dict[tuple[int, int], AgentMessage] = {}
    where, params = _scope_sql("m", scope)
    with db.connect() as conn:
        for chat_id, message_id in keys:
            row = conn.execute(
                f"SELECT {_MESSAGE_COLUMNS} FROM messages m WHERE m.chat_id = ? AND m.message_id = ?{where}",
                [chat_id, message_id, *params],
            ).fetchone()
            if row is not None:
                result[(chat_id, message_id)] = _message(row)
    return result


def context_window(db: Database, scope: Scope, chat_id: int, message_id: int, before: int, after: int) -> list[AgentMessage]:
    """Neighbouring messages by position (not id arithmetic), within the scope."""

    if chat_id not in scope.chat_ids:
        return []
    window_scope = replace(scope, media_types=None, sender_ids=None, sender_name=None)
    where, params = _scope_sql("m", window_scope)
    with db.connect() as conn:
        older = conn.execute(
            f"SELECT {_MESSAGE_COLUMNS} FROM messages m WHERE m.chat_id = ? AND m.message_id < ?{where} "
            "ORDER BY m.message_id DESC LIMIT ?",
            [chat_id, message_id, *params, before],
        ).fetchall()
        newer = conn.execute(
            f"SELECT {_MESSAGE_COLUMNS} FROM messages m WHERE m.chat_id = ? AND m.message_id >= ?{where} "
            "ORDER BY m.message_id ASC LIMIT ?",
            [chat_id, message_id, *params, after + 1],
        ).fetchall()
    return [_message(row) for row in reversed(older)] + [_message(row) for row in newer]


def period_messages(
    db: Database, scope: Scope, *, after_ids: dict[int, int] | None = None, limit: int = 100, newest_first: bool = False
) -> list[AgentMessage]:
    """Chronological messages in the scope, optionally after a per-chat cursor."""

    where, params = _scope_sql("m", scope)
    cursor_sql = ""
    cursor_params: list[Any] = []
    if after_ids:
        clauses = []
        for chat_id in scope.chat_ids:
            clauses.append("(m.chat_id = ? AND m.message_id > ?)")
            cursor_params.extend([chat_id, after_ids.get(chat_id, 0)])
        cursor_sql = f" AND ({' OR '.join(clauses)})"
    order = "DESC" if newest_first else "ASC"
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT {_MESSAGE_COLUMNS} FROM messages m WHERE 1 = 1{where}{cursor_sql} "
            f"ORDER BY m.date {order}, m.message_id {order} LIMIT ?",
            [*params, *cursor_params, limit],
        ).fetchall()
    return [_message(row) for row in rows]


def count_messages(db: Database, scope: Scope, *, after_ids: dict[int, int] | None = None) -> dict[int, int]:
    where, params = _scope_sql("m", scope)
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT m.chat_id, COUNT(*) AS n, MAX(m.message_id) AS newest FROM messages m WHERE 1 = 1{where} GROUP BY m.chat_id",
            params,
        ).fetchall()
        counts = {row["chat_id"]: row["n"] for row in rows}
        if after_ids:
            for chat_id in list(counts):
                counts[chat_id] = conn.execute(
                    f"SELECT COUNT(*) FROM messages m WHERE m.chat_id = ? AND m.message_id > ?{where}",
                    [chat_id, after_ids.get(chat_id, 0), *params],
                ).fetchone()[0]
    return {chat_id: count for chat_id, count in counts.items() if count}


def parse_ref(value: Any) -> tuple[int, int]:
    match = _CITATION_RE.match(str(value).strip())
    if not match:
        raise AgentQueryError(f"bad ref {value!r}: use tg://chat/<chat_id>/message/<id> or <chat_id>/<id>")
    return int(match.group(1)), int(match.group(2))


# ---------------------------------------------------------------- cursors


def load_cursor(db: Database, profile_id: str, client: str, chat_ids: Sequence[int]) -> dict[int, int]:
    if not chat_ids:
        return {}
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT chat_id, last_message_id FROM agent_cursors WHERE profile_id = ? AND client = ? "
            f"AND chat_id IN ({', '.join('?' for _ in chat_ids)})",
            [profile_id, client, *chat_ids],
        ).fetchall()
    return {row["chat_id"]: row["last_message_id"] for row in rows}


def save_cursor(db: Database, profile_id: str, client: str, shown: dict[int, int], seen_at: str) -> None:
    if not shown:
        return
    with db.connect() as conn:
        for chat_id, message_id in shown.items():
            conn.execute(
                """
                INSERT INTO agent_cursors(profile_id, client, chat_id, last_message_id, seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, client, chat_id) DO UPDATE SET
                    last_message_id = MAX(agent_cursors.last_message_id, excluded.last_message_id),
                    seen_at = excluded.seen_at
                """,
                (profile_id, client, chat_id, message_id, seen_at),
            )


# ---------------------------------------------------------------- helpers


def _scope_sql(alias: str, scope: Scope) -> tuple[str, list[Any]]:
    parts: list[str] = []
    params: list[Any] = []
    if not scope.chat_ids:
        return " AND 1 = 0", []
    parts.append(f"{alias}.chat_id IN ({', '.join('?' for _ in scope.chat_ids)})")
    params.extend(scope.chat_ids)
    if scope.since is not None:
        parts.append(f"{alias}.date >= ?")
        params.append(scope.since)
    if scope.until is not None:
        parts.append(f"{alias}.date <= ?")
        params.append(scope.until)
    if scope.allowed_media is not None:
        # Policy media limits hide disallowed media rows but never plain text messages.
        if scope.allowed_media:
            parts.append(f"({alias}.media_type IS NULL OR {alias}.media_type IN ({', '.join('?' for _ in scope.allowed_media)}))")
            params.extend(scope.allowed_media)
        else:
            parts.append(f"{alias}.media_type IS NULL")
    if scope.media_types is not None:
        if scope.media_types:
            parts.append(f"{alias}.media_type IN ({', '.join('?' for _ in scope.media_types)})")
            params.extend(scope.media_types)
        else:
            parts.append(f"{alias}.media_type IS NOT NULL")
    if scope.sender_ids is not None:
        parts.append(f"{alias}.sender_id IN ({', '.join('?' for _ in scope.sender_ids)})")
        params.extend(scope.sender_ids)
    if scope.sender_name:
        parts.append(f"{alias}.sender_name LIKE ?")
        params.append(f"%{scope.sender_name}%")
    return " AND " + " AND ".join(parts), params


def _message(row: sqlite3.Row) -> AgentMessage:
    return AgentMessage(
        chat_id=row["chat_id"],
        message_id=row["message_id"],
        date=row["date"],
        text=row["text"] or "",
        sender_id=row["sender_id"],
        sender_name=row["sender_name"],
        reply_to=row["reply_to_message_id"],
        forward_from=row["forward_from"],
        media_type=row["media_type"],
        transcript=row["transcript"],
    )
