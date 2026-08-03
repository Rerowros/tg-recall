from __future__ import annotations

import json
import sqlite3
import re
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .models import ChatRecord, JobRecord, MediaRecord, MessageRecord, SearchFilters, SearchResult
from .security import harden_path


SCHEMA_VERSION = 1


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chats (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    chat_type TEXT NOT NULL,
                    username TEXT,
                    is_eligible INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sync_scopes (
                    name TEXT PRIMARY KEY,
                    since TEXT,
                    until TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scope_chats (
                    scope_name TEXT NOT NULL REFERENCES sync_scopes(name) ON DELETE CASCADE,
                    chat_id INTEGER NOT NULL,
                    PRIMARY KEY (scope_name, chat_id)
                );

                CREATE TABLE IF NOT EXISTS sync_state (
                    chat_id INTEGER PRIMARY KEY,
                    newest_message_id INTEGER,
                    oldest_message_id INTEGER,
                    last_synced_at TEXT,
                    retry_after TEXT
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    text TEXT NOT NULL DEFAULT '',
                    sender_id INTEGER,
                    sender_name TEXT,
                    reply_to_message_id INTEGER,
                    forward_from TEXT,
                    edit_date TEXT,
                    has_media INTEGER NOT NULL DEFAULT 0,
                    media_type TEXT,
                    links_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL,
                    UNIQUE(chat_id, message_id)
                );

                CREATE TABLE IF NOT EXISTS media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    telegram_file_id TEXT,
                    media_type TEXT NOT NULL,
                    mime_type TEXT,
                    size_bytes INTEGER,
                    sha256 TEXT,
                    local_path TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(chat_id, message_id, media_type, telegram_file_id)
                );

                CREATE TABLE IF NOT EXISTS transcripts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    text TEXT NOT NULL,
                    language TEXT,
                    segments_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'success',
                    error_type TEXT,
                    retryable INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    chat_id INTEGER,
                    message_id INTEGER,
                    media_id INTEGER,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    retryable INTEGER NOT NULL DEFAULT 0,
                    retry_after TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    scope TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                    text,
                    chat_title,
                    tokenize='unicode61'
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
                    text,
                    tokenize='unicode61'
                );

                CREATE TABLE IF NOT EXISTS semantic_index (
                    source_type TEXT NOT NULL,
                    source_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    tokens_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source_type, source_id)
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, now_iso()),
            )
        harden_path(self.path, is_dir=False)

    def audit(self, event_type: str, scope: str | None = None, **details: Any) -> None:
        safe_details = redact_details(details)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_events(event_type, scope, details_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (event_type, scope, json.dumps(safe_details, ensure_ascii=False), now_iso()),
            )

    def upsert_chat(self, chat: ChatRecord) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO chats(chat_id, title, chat_type, username, is_eligible, updated_at)
                VALUES (:chat_id, :title, :chat_type, :username, :is_eligible, :updated_at)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title=excluded.title,
                    chat_type=excluded.chat_type,
                    username=excluded.username,
                    is_eligible=excluded.is_eligible,
                    updated_at=excluded.updated_at
                """,
                {**asdict(chat), "is_eligible": int(chat.is_eligible), "updated_at": now_iso()},
            )

    def list_chats(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute("SELECT * FROM chats ORDER BY lower(title)"))

    def create_scope(self, name: str, chat_ids: list[int], since: str | None, until: str | None) -> None:
        if not chat_ids:
            raise ValueError("Scope must include at least one chat")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_scopes(name, since, until, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET since=excluded.since, until=excluded.until
                """,
                (name, since, until, now_iso()),
            )
            conn.execute("DELETE FROM scope_chats WHERE scope_name = ?", (name,))
            conn.executemany(
                "INSERT INTO scope_chats(scope_name, chat_id) VALUES (?, ?)",
                [(name, chat_id) for chat_id in chat_ids],
            )

    def get_scope(self, name: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM sync_scopes WHERE name = ?", (name,)).fetchone()
            if not row:
                return None
            chat_ids = [
                item["chat_id"]
                for item in conn.execute(
                    "SELECT chat_id FROM scope_chats WHERE scope_name = ? ORDER BY chat_id",
                    (name,),
                )
            ]
            return {"name": row["name"], "since": row["since"], "until": row["until"], "chat_ids": chat_ids}

    def list_scopes(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            scopes = []
            for row in conn.execute("SELECT * FROM sync_scopes ORDER BY name"):
                chats = [
                    item["chat_id"]
                    for item in conn.execute(
                        "SELECT chat_id FROM scope_chats WHERE scope_name = ? ORDER BY chat_id",
                        (row["name"],),
                    )
                ]
                scopes.append({"name": row["name"], "since": row["since"], "until": row["until"], "chat_ids": chats})
            return scopes

    def upsert_message(self, message: MessageRecord) -> None:
        with self.connect() as conn:
            chat_title = self._chat_title(conn, message.chat_id)
            conn.execute(
                """
                INSERT INTO messages(
                    chat_id, message_id, date, text, sender_id, sender_name,
                    reply_to_message_id, forward_from, edit_date, has_media, media_type,
                    links_json, updated_at
                )
                VALUES (
                    :chat_id, :message_id, :date, :text, :sender_id, :sender_name,
                    :reply_to_message_id, :forward_from, :edit_date, :has_media, :media_type,
                    :links_json, :updated_at
                )
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    date=excluded.date,
                    text=excluded.text,
                    sender_id=excluded.sender_id,
                    sender_name=excluded.sender_name,
                    reply_to_message_id=excluded.reply_to_message_id,
                    forward_from=excluded.forward_from,
                    edit_date=excluded.edit_date,
                    has_media=excluded.has_media,
                    media_type=excluded.media_type,
                    links_json=excluded.links_json,
                    updated_at=excluded.updated_at
                """,
                {
                    **asdict(message),
                    "date": message.date.isoformat(),
                    "edit_date": message.edit_date.isoformat() if message.edit_date else None,
                    "has_media": int(message.has_media),
                    "updated_at": now_iso(),
                },
            )
            row = conn.execute(
                "SELECT id FROM messages WHERE chat_id = ? AND message_id = ?",
                (message.chat_id, message.message_id),
            ).fetchone()
            if row:
                conn.execute("DELETE FROM messages_fts WHERE rowid = ?", (row["id"],))
                conn.execute(
                    "INSERT INTO messages_fts(rowid, text, chat_title) VALUES (?, ?, ?)",
                    (row["id"], message.text, chat_title),
                )
                self._upsert_semantic(conn, "message", row["id"], message.chat_id, message.message_id, message.text)

    def update_sync_state(
        self,
        chat_id: int,
        newest_message_id: int | None = None,
        oldest_message_id: int | None = None,
        retry_after: str | None = None,
    ) -> None:
        with self.connect() as conn:
            current = conn.execute("SELECT * FROM sync_state WHERE chat_id = ?", (chat_id,)).fetchone()
            if current:
                newest = max(filter(None, [current["newest_message_id"], newest_message_id]), default=None)
                oldest_values = [value for value in [current["oldest_message_id"], oldest_message_id] if value]
                oldest = min(oldest_values) if oldest_values else None
                conn.execute(
                    """
                    UPDATE sync_state
                    SET newest_message_id = ?, oldest_message_id = ?, last_synced_at = ?, retry_after = ?
                    WHERE chat_id = ?
                    """,
                    (newest, oldest, now_iso(), retry_after, chat_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO sync_state(chat_id, newest_message_id, oldest_message_id, last_synced_at, retry_after)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (chat_id, newest_message_id, oldest_message_id, now_iso(), retry_after),
                )

    def enqueue_media(self, chat_id: int, message_id: int, media_type: str, telegram_file_id: str | None = None) -> int:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO media(
                    chat_id, message_id, telegram_file_id, media_type, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (chat_id, message_id, telegram_file_id, media_type, now_iso(), now_iso()),
            )
            row = conn.execute(
                """
                SELECT id FROM media
                WHERE chat_id = ? AND message_id = ? AND media_type = ? AND
                      (telegram_file_id = ? OR (telegram_file_id IS NULL AND ? IS NULL))
                """,
                (chat_id, message_id, media_type, telegram_file_id, telegram_file_id),
            ).fetchone()
            media_id = int(row["id"])
            conn.execute(
                """
                INSERT INTO jobs(stage, status, chat_id, message_id, media_id, created_at, updated_at)
                VALUES ('media_download', 'pending', ?, ?, ?, ?, ?)
                """,
                (chat_id, message_id, media_id, now_iso(), now_iso()),
            )
            return media_id

    def get_pending_jobs(self, stage: str, limit: int = 20) -> list[JobRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, stage, status, chat_id, message_id, media_id, payload_json
                FROM jobs
                WHERE stage = ? AND status IN ('pending', 'retry')
                  AND (retry_after IS NULL OR retry_after <= ?)
                ORDER BY id
                LIMIT ?
                """,
                (stage, now_iso(), limit),
            ).fetchall()
            return [
                JobRecord(
                    id=row["id"],
                    stage=row["stage"],
                    status=row["status"],
                    chat_id=row["chat_id"],
                    message_id=row["message_id"],
                    media_id=row["media_id"],
                    payload_json=row["payload_json"],
                )
                for row in rows
            ]

    def update_job(self, job_id: int, status: str, error: str | None = None, retryable: bool = False, retry_after: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, error = ?, retryable = ?, retry_after = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, error, int(retryable), retry_after, now_iso(), job_id),
            )

    def enqueue_transcription(self, media_id: int) -> None:
        with self.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM jobs WHERE stage = 'transcription' AND media_id = ? AND status IN ('pending', 'retry')",
                (media_id,),
            ).fetchone()
            if exists:
                return
            media = conn.execute("SELECT chat_id, message_id FROM media WHERE id = ?", (media_id,)).fetchone()
            if not media:
                raise ValueError(f"Unknown media id: {media_id}")
            conn.execute(
                """
                INSERT INTO jobs(stage, status, chat_id, message_id, media_id, created_at, updated_at)
                VALUES ('transcription', 'pending', ?, ?, ?, ?, ?)
                """,
                (media["chat_id"], media["message_id"], media_id, now_iso(), now_iso()),
            )

    def get_media(self, media_id: int) -> MediaRecord | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
            if not row:
                return None
            return MediaRecord(
                id=row["id"],
                chat_id=row["chat_id"],
                message_id=row["message_id"],
                telegram_file_id=row["telegram_file_id"],
                media_type=row["media_type"],
                mime_type=row["mime_type"],
                size_bytes=row["size_bytes"],
                sha256=row["sha256"],
                local_path=row["local_path"],
                status=row["status"],
            )

    def update_media_downloaded(
        self,
        media_id: int,
        *,
        local_path: str,
        sha256: str,
        size_bytes: int,
        mime_type: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE media
                SET local_path = ?, sha256 = ?, size_bytes = ?, mime_type = COALESCE(?, mime_type),
                    status = 'downloaded', updated_at = ?
                WHERE id = ?
                """,
                (local_path, sha256, size_bytes, mime_type, now_iso(), media_id),
            )
            media = conn.execute("SELECT chat_id, message_id FROM media WHERE id = ?", (media_id,)).fetchone()
            exists = conn.execute(
                "SELECT 1 FROM jobs WHERE stage = 'transcription' AND media_id = ? AND status IN ('pending', 'retry')",
                (media_id,),
            ).fetchone()
            if media and not exists:
                conn.execute(
                    """
                    INSERT INTO jobs(stage, status, chat_id, message_id, media_id, created_at, updated_at)
                    VALUES ('transcription', 'pending', ?, ?, ?, ?, ?)
                    """,
                    (media["chat_id"], media["message_id"], media_id, now_iso(), now_iso()),
                )

    def mark_media_failed(self, media_id: int, error: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE media SET status = 'failed', updated_at = ? WHERE id = ?",
                (now_iso(), media_id),
            )
            conn.execute(
                """
                INSERT INTO jobs(stage, status, media_id, error, retryable, created_at, updated_at)
                VALUES ('media_download', 'failed', ?, ?, 1, ?, ?)
                """,
                (media_id, error, now_iso(), now_iso()),
            )

    def find_media_by_sha256(self, digest: str) -> MediaRecord | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM media WHERE sha256 = ? AND local_path IS NOT NULL LIMIT 1", (digest,)).fetchone()
            if not row:
                return None
            return MediaRecord(
                id=row["id"],
                chat_id=row["chat_id"],
                message_id=row["message_id"],
                telegram_file_id=row["telegram_file_id"],
                media_type=row["media_type"],
                mime_type=row["mime_type"],
                size_bytes=row["size_bytes"],
                sha256=row["sha256"],
                local_path=row["local_path"],
                status=row["status"],
            )

    def insert_transcript(
        self,
        media_id: int,
        provider: str,
        text: str,
        language: str | None = None,
        segments: list[dict[str, Any]] | None = None,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO transcripts(media_id, provider, text, language, segments_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (media_id, provider, text, language, json.dumps(segments or []), now_iso()),
            )
            transcript_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO transcripts_fts(rowid, text) VALUES (?, ?)",
                (transcript_id, text),
            )
            media = conn.execute("SELECT chat_id, message_id FROM media WHERE id = ?", (media_id,)).fetchone()
            if media:
                self._upsert_semantic(conn, "transcript", transcript_id, media["chat_id"], media["message_id"], text)
            return transcript_id

    def search(
        self,
        query: str,
        limit: int = 20,
        chat_id: int | None = None,
        filters: SearchFilters | None = None,
    ) -> list[SearchResult]:
        filters = filters or SearchFilters(chat_id=chat_id)
        if chat_id is not None and filters.chat_id is None:
            filters = SearchFilters(
                chat_id=chat_id,
                sender_id=filters.sender_id,
                since=filters.since,
                until=filters.until,
                media_type=filters.media_type,
                has_link=filters.has_link,
            )
        with self.connect() as conn:
            where, extra = _message_filter_sql("m", filters)
            params: list[Any] = [query, *extra]
            params.append(limit)
            message_rows = conn.execute(
                f"""
                SELECT m.chat_id, COALESCE(c.title, '') AS chat_title, m.message_id,
                       m.date, snippet(messages_fts, 0, '[', ']', '...', 12) AS snippet,
                       NULL AS media_id, NULL AS transcript_id,
                       bm25(messages_fts) AS rank
                FROM messages_fts
                JOIN messages m ON m.id = messages_fts.rowid
                LEFT JOIN chats c ON c.chat_id = m.chat_id
                WHERE messages_fts MATCH ?{where}
                ORDER BY rank
                LIMIT ?
                """,
                params,
            ).fetchall()

            transcript_where, transcript_extra = _message_filter_sql("m", filters, media_alias="media")
            transcript_params: list[Any] = [query, *transcript_extra]
            transcript_params.append(limit)
            transcript_rows = conn.execute(
                f"""
                SELECT media.chat_id, COALESCE(c.title, '') AS chat_title, media.message_id,
                       COALESCE(m.date, t.created_at) AS date,
                       snippet(transcripts_fts, 0, '[', ']', '...', 12) AS snippet,
                       media.id AS media_id, t.id AS transcript_id,
                       bm25(transcripts_fts) AS rank
                FROM transcripts_fts
                JOIN transcripts t ON t.id = transcripts_fts.rowid
                JOIN media ON media.id = t.media_id
                LEFT JOIN messages m ON m.chat_id = media.chat_id AND m.message_id = media.message_id
                LEFT JOIN chats c ON c.chat_id = media.chat_id
                WHERE transcripts_fts MATCH ?{transcript_where}
                ORDER BY rank
                LIMIT ?
                """,
                transcript_params,
            ).fetchall()

            rows = sorted([*message_rows, *transcript_rows], key=lambda row: row["rank"] or 0)[:limit]
            return [
                SearchResult(
                    chat_id=row["chat_id"],
                    chat_title=row["chat_title"],
                    message_id=row["message_id"],
                    timestamp=row["date"],
                    text=row["snippet"],
                    media_id=row["media_id"],
                    transcript_id=row["transcript_id"],
                    rank=row["rank"],
                )
                for row in rows
            ]

    def semantic_search(self, query: str, limit: int = 10, filters: SearchFilters | None = None) -> list[SearchResult]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        filters = filters or SearchFilters()
        where_parts = []
        params: list[Any] = []
        if filters.chat_id is not None:
            where_parts.append("si.chat_id = ?")
            params.append(filters.chat_id)
        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT si.source_type, si.source_id, si.chat_id, si.message_id, si.text, si.tokens_json,
                       COALESCE(c.title, '') AS chat_title, COALESCE(m.date, si.updated_at) AS date
                FROM semantic_index si
                LEFT JOIN chats c ON c.chat_id = si.chat_id
                LEFT JOIN messages m ON m.chat_id = si.chat_id AND m.message_id = si.message_id
                {where}
                """,
                params,
            ).fetchall()
        scored = []
        for row in rows:
            tokens = set(json.loads(row["tokens_json"]))
            score = len(query_tokens & tokens) / max(len(query_tokens), 1)
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        results = []
        for score, row in scored[:limit]:
            results.append(
                SearchResult(
                    chat_id=row["chat_id"],
                    chat_title=row["chat_title"],
                    message_id=row["message_id"],
                    timestamp=row["date"],
                    text=row["text"][:240],
                    transcript_id=row["source_id"] if row["source_type"] == "transcript" else None,
                    rank=-score,
                )
            )
        return results

    def rebuild_indexes(self) -> dict[str, int]:
        with self.connect() as conn:
            conn.execute("DELETE FROM messages_fts")
            conn.execute("DELETE FROM transcripts_fts")
            conn.execute("DELETE FROM semantic_index")
            messages = conn.execute(
                """
                SELECT m.id, m.chat_id, m.message_id, m.text, COALESCE(c.title, '') AS chat_title
                FROM messages m
                LEFT JOIN chats c ON c.chat_id = m.chat_id
                """
            ).fetchall()
            for row in messages:
                conn.execute(
                    "INSERT INTO messages_fts(rowid, text, chat_title) VALUES (?, ?, ?)",
                    (row["id"], row["text"], row["chat_title"]),
                )
                self._upsert_semantic(conn, "message", row["id"], row["chat_id"], row["message_id"], row["text"])
            transcripts = conn.execute(
                """
                SELECT t.id, t.text, media.chat_id, media.message_id
                FROM transcripts t
                JOIN media ON media.id = t.media_id
                """
            ).fetchall()
            for row in transcripts:
                conn.execute("INSERT INTO transcripts_fts(rowid, text) VALUES (?, ?)", (row["id"], row["text"]))
                self._upsert_semantic(conn, "transcript", row["id"], row["chat_id"], row["message_id"], row["text"])
            return {"messages": len(messages), "transcripts": len(transcripts)}

    def purge_chat(self, chat_id: int) -> dict[str, int]:
        with self.connect() as conn:
            message_ids = [row["id"] for row in conn.execute("SELECT id FROM messages WHERE chat_id = ?", (chat_id,))]
            transcript_ids = [
                row["id"]
                for row in conn.execute(
                    """
                    SELECT t.id
                    FROM transcripts t
                    JOIN media ON media.id = t.media_id
                    WHERE media.chat_id = ?
                    """,
                    (chat_id,),
                )
            ]
            for row_id in message_ids:
                conn.execute("DELETE FROM messages_fts WHERE rowid = ?", (row_id,))
            for row_id in transcript_ids:
                conn.execute("DELETE FROM transcripts_fts WHERE rowid = ?", (row_id,))
            media_count = conn.execute("SELECT COUNT(*) AS count FROM media WHERE chat_id = ?", (chat_id,)).fetchone()["count"]
            message_count = len(message_ids)
            conn.execute("DELETE FROM transcripts WHERE media_id IN (SELECT id FROM media WHERE chat_id = ?)", (chat_id,))
            conn.execute("DELETE FROM media WHERE chat_id = ?", (chat_id,))
            conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
            conn.execute("DELETE FROM scope_chats WHERE chat_id = ?", (chat_id,))
            conn.execute("DELETE FROM sync_state WHERE chat_id = ?", (chat_id,))
            conn.execute("DELETE FROM chats WHERE chat_id = ?", (chat_id,))
            return {"messages": message_count, "media": media_count, "transcripts": len(transcript_ids)}

    def purge_all(self) -> None:
        with self.connect() as conn:
            for table in [
                "messages_fts",
                "transcripts_fts",
                "transcripts",
                "media",
                "messages",
                "jobs",
                "scope_chats",
                "sync_scopes",
                "sync_state",
                "chats",
                "audit_events",
            ]:
                conn.execute(f"DELETE FROM {table}")

    def message_context(self, chat_id: int, message_id: int, radius: int = 3) -> list[SearchResult]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT m.chat_id, COALESCE(c.title, '') AS chat_title, m.message_id, m.date, m.text
                FROM messages m
                LEFT JOIN chats c ON c.chat_id = m.chat_id
                WHERE m.chat_id = ? AND m.message_id BETWEEN ? AND ?
                ORDER BY m.message_id
                """,
                (chat_id, message_id - radius, message_id + radius),
            ).fetchall()
            return [
                SearchResult(
                    chat_id=row["chat_id"],
                    chat_title=row["chat_title"],
                    message_id=row["message_id"],
                    timestamp=row["date"],
                    text=row["text"],
                )
                for row in rows
            ]

    def _chat_title(self, conn: sqlite3.Connection, chat_id: int) -> str:
        row = conn.execute("SELECT title FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
        return row["title"] if row else ""

    def _upsert_semantic(
        self,
        conn: sqlite3.Connection,
        source_type: str,
        source_id: int,
        chat_id: int,
        message_id: int,
        text: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO semantic_index(source_type, source_id, chat_id, message_id, text, tokens_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_type, source_id) DO UPDATE SET
                chat_id=excluded.chat_id,
                message_id=excluded.message_id,
                text=excluded.text,
                tokens_json=excluded.tokens_json,
                updated_at=excluded.updated_at
            """,
            (source_type, source_id, chat_id, message_id, text, json.dumps(sorted(tokenize(text))), now_iso()),
        )


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def redact_details(details: dict[str, Any]) -> dict[str, Any]:
    sensitive = {"api_hash", "phone", "session", "session_path", "password", "code"}
    redacted: dict[str, Any] = {}
    for key, value in details.items():
        if key in sensitive and value not in (None, ""):
            redacted[key] = "***REDACTED***"
        elif isinstance(value, dict):
            redacted[key] = redact_details(value)
        else:
            redacted[key] = value
    return redacted


def tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r"[\w']+", text.lower()) if len(token) > 2}


def _message_filter_sql(
    message_alias: str,
    filters: SearchFilters,
    *,
    media_alias: str | None = None,
) -> tuple[str, list[Any]]:
    parts: list[str] = []
    params: list[Any] = []
    chat_alias = media_alias or message_alias
    if filters.chat_id is not None:
        parts.append(f"{chat_alias}.chat_id = ?")
        params.append(filters.chat_id)
    if filters.sender_id is not None:
        parts.append(f"{message_alias}.sender_id = ?")
        params.append(filters.sender_id)
    if filters.since is not None:
        parts.append(f"{message_alias}.date >= ?")
        params.append(filters.since)
    if filters.until is not None:
        parts.append(f"{message_alias}.date <= ?")
        params.append(filters.until)
    if filters.media_type is not None:
        parts.append(f"{chat_alias}.media_type = ?" if media_alias else f"{message_alias}.media_type = ?")
        params.append(filters.media_type)
    if filters.has_link is not None:
        parts.append(f"{message_alias}.links_json != '[]'" if filters.has_link else f"{message_alias}.links_json = '[]'")
    return (f" AND {' AND '.join(parts)}" if parts else "", params)
