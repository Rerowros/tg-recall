from __future__ import annotations

import json
import sqlite3
import hashlib
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Sequence

from .models import ChatRecord, JobRecord, MediaRecord, MessageRecord, SearchFilters
from .security import harden_path


_UNSET = object()


SCHEMA_VERSION = 7
SCHEMA_BASELINE_VERSION = 2


class SchemaCompatibilityError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        payload = json.dumps({"version": self.version, "name": self.name, "statements": self.statements}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


MIGRATION_REGISTRY: tuple[Migration, ...] = (
    Migration(2, "v0.2-bootstrap", ()),
    Migration(3, "maintenance-indexes-v1", (
        "CREATE INDEX IF NOT EXISTS jobs_status_retry_idx ON jobs(status, retryable, retry_after)",
        "CREATE INDEX IF NOT EXISTS jobs_filter_idx ON jobs(stage, chat_id, updated_at)",
        "CREATE INDEX IF NOT EXISTS messages_filter_idx ON messages(chat_id, sender_id, date, media_type)",
    )),
    Migration(4, "private-wiki-metadata-v1", (
        """CREATE TABLE wiki_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            scope_hash TEXT NOT NULL,
            source_version TEXT NOT NULL,
            source_cursor_json TEXT NOT NULL,
            raw_path TEXT NOT NULL,
            raw_sha256 TEXT NOT NULL,
            record_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(profile_id, scope_id, snapshot_id)
        )""",
        """CREATE TABLE wiki_page_revisions (
            revision_id TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL REFERENCES wiki_snapshots(snapshot_id) ON DELETE RESTRICT,
            profile_id TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            page_kind TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            page_path TEXT NOT NULL,
            page_sha256 TEXT NOT NULL,
            source_version TEXT NOT NULL,
            freshness_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(profile_id, scope_id, revision_id)
        )""",
        """CREATE TABLE wiki_assertions (
            assertion_id TEXT NOT NULL,
            revision_id TEXT NOT NULL REFERENCES wiki_page_revisions(revision_id) ON DELETE CASCADE,
            assertion_kind TEXT NOT NULL,
            confidence REAL NOT NULL,
            PRIMARY KEY(assertion_id, revision_id)
        )""",
        """CREATE TABLE wiki_assertion_citations (
            assertion_id TEXT NOT NULL,
            revision_id TEXT NOT NULL,
            citation TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            source_version TEXT NOT NULL,
            PRIMARY KEY(assertion_id, revision_id, citation),
            FOREIGN KEY(assertion_id, revision_id) REFERENCES wiki_assertions(assertion_id, revision_id) ON DELETE CASCADE
        )""",
        "CREATE INDEX wiki_snapshots_scope_idx ON wiki_snapshots(profile_id, scope_id, created_at)",
        "CREATE INDEX wiki_revisions_scope_idx ON wiki_page_revisions(profile_id, scope_id, page_kind, subject_id, updated_at)",
        "CREATE INDEX wiki_citations_lookup_idx ON wiki_assertion_citations(chat_id, message_id)",
    )),
    Migration(5, "local-hybrid-retrieval-v1", (
        """CREATE TABLE IF NOT EXISTS embedding_models (
            model_identity TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            model_id TEXT NOT NULL,
            dimensions INTEGER NOT NULL CHECK(dimensions > 0),
            model_source_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS embedding_vectors (
            source_type TEXT NOT NULL CHECK(source_type IN ('message', 'transcript')),
            source_id INTEGER NOT NULL,
            model_identity TEXT NOT NULL REFERENCES embedding_models(model_identity) ON DELETE CASCADE,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            source_hash TEXT NOT NULL,
            vector_encoding TEXT NOT NULL DEFAULT 'json-float-v1',
            vector_json TEXT NOT NULL,
            indexed_at TEXT NOT NULL,
            PRIMARY KEY(source_type, source_id, model_identity)
        )""",
        """CREATE TABLE IF NOT EXISTS embedding_index_checkpoints (
            model_identity TEXT PRIMARY KEY REFERENCES embedding_models(model_identity) ON DELETE CASCADE,
            completed_batches INTEGER NOT NULL DEFAULT 0,
            last_source_type TEXT CHECK(last_source_type IN ('message', 'transcript')),
            last_source_id INTEGER,
            last_source_hash TEXT,
            updated_at TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS embedding_vectors_model_source_idx ON embedding_vectors(model_identity, source_type, source_id)",
        "CREATE INDEX IF NOT EXISTS embedding_vectors_model_chat_idx ON embedding_vectors(model_identity, chat_id, message_id)",
    )),
    Migration(6, "agent-context-memory-routing-v1", (
        """CREATE TABLE IF NOT EXISTS knowledge_profiles (
            profile_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS evidence_sets (
            profile_id TEXT NOT NULL REFERENCES knowledge_profiles(profile_id) ON DELETE CASCADE,
            evidence_set_id TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            scope_chat_ids_json TEXT NOT NULL,
            purpose TEXT NOT NULL,
            query TEXT NOT NULL,
            summary TEXT NOT NULL,
            revision TEXT NOT NULL,
            topics_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(profile_id, evidence_set_id)
        )""",
        """CREATE TABLE IF NOT EXISTS evidence_set_members (
            profile_id TEXT NOT NULL,
            evidence_set_id TEXT NOT NULL,
            member_id TEXT NOT NULL,
            member_kind TEXT NOT NULL CHECK(member_kind IN ('raw', 'wiki')),
            logical_id TEXT NOT NULL,
            member_version TEXT NOT NULL,
            PRIMARY KEY(profile_id, evidence_set_id, member_id),
            FOREIGN KEY(profile_id, evidence_set_id)
                REFERENCES evidence_sets(profile_id, evidence_set_id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS evidence_member_sources (
            profile_id TEXT NOT NULL,
            evidence_set_id TEXT NOT NULL,
            member_id TEXT NOT NULL,
            citation TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            source_version TEXT NOT NULL,
            PRIMARY KEY(profile_id, evidence_set_id, member_id, citation),
            FOREIGN KEY(profile_id, evidence_set_id, member_id)
                REFERENCES evidence_set_members(profile_id, evidence_set_id, member_id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS research_sessions (
            profile_id TEXT NOT NULL REFERENCES knowledge_profiles(profile_id) ON DELETE CASCADE,
            session_id TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            scope_chat_ids_json TEXT NOT NULL,
            purpose TEXT NOT NULL,
            budgets_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(profile_id, session_id)
        )""",
        """CREATE TABLE IF NOT EXISTS research_session_checkpoints (
            profile_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            checkpoint_at TEXT NOT NULL,
            summary TEXT NOT NULL,
            decisions_json TEXT NOT NULL,
            unresolved_questions_json TEXT NOT NULL,
            evidence_set_ids_json TEXT NOT NULL,
            PRIMARY KEY(profile_id, session_id, checkpoint_at),
            FOREIGN KEY(profile_id, session_id)
                REFERENCES research_sessions(profile_id, session_id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS research_session_telemetry (
            telemetry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            stage TEXT NOT NULL CHECK(stage IN ('reused_evidence', 'catalog', 'raw', 'expansion', 'media')),
            retrieval_calls INTEGER NOT NULL CHECK(retrieval_calls >= 0),
            returned_items INTEGER NOT NULL CHECK(returned_items >= 0),
            deduplicated_items INTEGER NOT NULL CHECK(deduplicated_items >= 0),
            retries INTEGER NOT NULL CHECK(retries >= 0),
            latency_ms INTEGER NOT NULL CHECK(latency_ms >= 0),
            estimated_tokens INTEGER NOT NULL CHECK(estimated_tokens >= 0),
            counter TEXT NOT NULL,
            counter_version TEXT NOT NULL,
            safety_margin REAL NOT NULL CHECK(safety_margin >= 0 AND safety_margin < 1),
            reused_evidence INTEGER NOT NULL CHECK(reused_evidence IN (0, 1)),
            sufficient INTEGER NOT NULL CHECK(sufficient IN (0, 1)),
            created_at TEXT NOT NULL,
            FOREIGN KEY(profile_id, session_id)
                REFERENCES research_sessions(profile_id, session_id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS research_session_actual_usage (
            telemetry_id INTEGER PRIMARY KEY REFERENCES research_session_telemetry(telemetry_id) ON DELETE CASCADE,
            usage_source TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER CHECK(input_tokens IS NULL OR input_tokens >= 0),
            cached_input_tokens INTEGER CHECK(cached_input_tokens IS NULL OR cached_input_tokens >= 0),
            output_tokens INTEGER CHECK(output_tokens IS NULL OR output_tokens >= 0),
            reasoning_tokens INTEGER CHECK(reasoning_tokens IS NULL OR reasoning_tokens >= 0),
            recorded_at TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS evidence_sets_scope_created_idx ON evidence_sets(profile_id, scope_id, created_at DESC, evidence_set_id)",
        "CREATE INDEX IF NOT EXISTS evidence_member_sources_citation_idx ON evidence_member_sources(profile_id, citation, source_version)",
        "CREATE INDEX IF NOT EXISTS research_sessions_scope_updated_idx ON research_sessions(profile_id, scope_id, updated_at DESC, session_id)",
        "CREATE INDEX IF NOT EXISTS research_telemetry_session_idx ON research_session_telemetry(profile_id, session_id, telemetry_id DESC)",
    )),
    Migration(7, "agent-ux-v2", (
        """CREATE TABLE IF NOT EXISTS agent_cursors (
            profile_id TEXT NOT NULL,
            client TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            last_message_id INTEGER NOT NULL,
            seen_at TEXT NOT NULL,
            PRIMARY KEY(profile_id, client, chat_id)
        )""",
        """CREATE TABLE IF NOT EXISTS agent_meta (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS messages_chat_date_idx ON messages(chat_id, date)",
        "CREATE INDEX IF NOT EXISTS messages_chat_topic_idx ON messages(chat_id, topic_id, date)",
        """CREATE TABLE IF NOT EXISTS forum_topics (
            chat_id INTEGER NOT NULL,
            topic_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(chat_id, topic_id)
        )""",
    )),
)


# Children before parents (foreign keys are on).
_OBSOLETE_TABLES = (
    "semantic_index",
    "embedding_vectors",
    "embedding_index_checkpoints",
    "embedding_models",
    "scope_chats",
    "sync_scopes",
    "wiki_assertion_citations",
    "wiki_assertions",
    "wiki_page_revisions",
    "wiki_snapshots",
    "research_session_actual_usage",
    "research_session_telemetry",
    "research_session_checkpoints",
    "research_sessions",
    "evidence_member_sources",
    "evidence_set_members",
    "evidence_sets",
    "knowledge_profiles",
)
_DISPOSABLE_TABLES = frozenset(
    {"semantic_index", "embedding_vectors", "embedding_index_checkpoints", "embedding_models", "scope_chats", "sync_scopes"}
)


def _validate_migration_registry() -> None:
    versions = [migration.version for migration in MIGRATION_REGISTRY]
    expected = list(range(SCHEMA_BASELINE_VERSION, SCHEMA_VERSION + 1))
    if versions != expected or len(set(versions)) != len(versions):
        raise SchemaCompatibilityError(
            "migration registry must contain each ordered version from the supported baseline through SCHEMA_VERSION"
        )


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
            self._preflight_migration_history(conn)
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
                    storage_key TEXT,
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
                """
            )
            self._ensure_column(conn, "schema_migrations", "checksum", "TEXT")
            self._ensure_column(conn, "media", "storage_key", "TEXT")
            self._ensure_column(conn, "messages", "topic_id", "INTEGER")
            # v0.2 used the same core tables but recorded only an opaque
            # current version. Bootstrap it without touching archive rows.
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, checksum, applied_at) VALUES (?, ?, ?)",
                (2, MIGRATION_REGISTRY[0].checksum, now_iso()),
            )
            conn.execute(
                "UPDATE schema_migrations SET checksum = COALESCE(checksum, ?) WHERE version = 2",
                (MIGRATION_REGISTRY[0].checksum,),
            )
            # A development/pre-history wiki build may have created the v4
            # metadata tables before history was introduced. Treat that shape
            # as a supported bootstrap without rewriting archive identities.
            if self._table_exists(conn, "wiki_snapshots"):
                for version in (3, 4):
                    migration = next(item for item in MIGRATION_REGISTRY if item.version == version)
                    conn.execute(
                        "INSERT OR IGNORE INTO schema_migrations(version, checksum, applied_at) VALUES (?, ?, ?)",
                        (migration.version, migration.checksum, now_iso()),
                    )
            current = int(conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0])
            if current > SCHEMA_VERSION:
                raise SchemaCompatibilityError(f"archive schema version {current} is newer than supported {SCHEMA_VERSION}")
            for migration in MIGRATION_REGISTRY:
                applied = conn.execute("SELECT checksum FROM schema_migrations WHERE version = ?", (migration.version,)).fetchone()
                if applied and applied["checksum"] != migration.checksum:
                    raise SchemaCompatibilityError(f"archive migration {migration.version} checksum is incompatible")
                if migration.version <= current:
                    continue
                self._apply_migration(conn, migration)
            reclaimed = self._drop_obsolete_tables(conn)
        if reclaimed:
            with sqlite3.connect(self.path) as vacuum:
                vacuum.execute("VACUUM")
        harden_path(self.path, is_dir=False)

    @classmethod
    def _drop_obsolete_tables(cls, conn: sqlite3.Connection) -> bool:
        """Drop tables of features removed in 0.7; returns whether space is worth reclaiming.

        Derived indexes and sync scopes go unconditionally. Wiki and research
        tables are dropped only when empty, so nobody loses authored data.
        """

        reclaimed = False
        for table in _OBSOLETE_TABLES:
            if not cls._table_exists(conn, table):
                continue
            rows = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if rows and table not in _DISPOSABLE_TABLES:
                continue
            conn.execute(f"DROP TABLE {table}")
            reclaimed = reclaimed or rows > 1000
        return reclaimed

    @staticmethod
    def _preflight_migration_history(conn: sqlite3.Connection) -> None:
        """Abort newer/incompatible histories before any CREATE/ALTER statement."""

        _validate_migration_registry()
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'").fetchone()
        if not exists:
            return
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(schema_migrations)")}
        if "version" not in columns:
            raise SchemaCompatibilityError("archive migration history is incompatible")
        current = int(conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0])
        if current > SCHEMA_VERSION:
            raise SchemaCompatibilityError(f"archive schema version {current} is newer than supported {SCHEMA_VERSION}")
        if "checksum" not in columns:
            return
        known = {migration.version: migration for migration in MIGRATION_REGISTRY}
        for row in conn.execute("SELECT version, checksum FROM schema_migrations WHERE checksum IS NOT NULL"):
            migration = known.get(row["version"])
            if migration and row["checksum"] != migration.checksum:
                raise SchemaCompatibilityError(f"archive migration {migration.version} checksum is incompatible")

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None

    @staticmethod
    def _apply_migration(conn: sqlite3.Connection, migration: Migration) -> None:
        """Apply one registry entry in a savepoint, including its history row."""

        conn.execute("SAVEPOINT schema_migration")
        try:
            for statement in migration.statements:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations(version, checksum, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.checksum, now_iso()),
            )
            conn.execute(
                "INSERT INTO audit_events(event_type, scope, details_json, created_at) VALUES (?, ?, ?, ?)",
                ("schema_migration_applied", str(migration.version), json.dumps({"version": migration.version, "checksum": migration.checksum}), now_iso()),
            )
            conn.execute("RELEASE SAVEPOINT schema_migration")
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT schema_migration")
            conn.execute("RELEASE SAVEPOINT schema_migration")
            raise

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def set_meta(self, key: str, value: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO agent_meta(key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, value, now_iso()),
            )

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

    def health(self) -> dict[str, Any]:
        with self.connect() as conn:
            integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
            counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("chats", "messages", "media", "transcripts", "jobs")
            }
            latest = conn.execute("SELECT MAX(last_synced_at) FROM sync_state").fetchone()[0]
        return {"integrity": integrity, "counts": counts, "last_synced_at": latest}

    def diagnostics(self) -> dict[str, Any]:
        """Sanitized operational checks; no message text or absolute paths."""
        with self.connect() as conn:
            applied = list(conn.execute("SELECT version, checksum, applied_at FROM schema_migrations ORDER BY version"))
            current = max((row["version"] for row in applied), default=0)
            compatibility = "ok" if current == SCHEMA_VERSION else ("newer" if current > SCHEMA_VERSION else "pending")
            integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
            queue = {
                row["status"]: row["count"]
                for row in conn.execute("SELECT status, COUNT(*) AS count FROM jobs GROUP BY status")
            }
            stale_processing = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'processing' AND updated_at < ?",
                ((datetime.now(UTC) - timedelta(hours=1)).isoformat(),),
            ).fetchone()[0]
            missing_media_jobs = conn.execute(
                """SELECT COUNT(*) FROM media m WHERE m.status = 'pending'
                   AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.stage = 'media_download' AND j.media_id = m.id)"""
            ).fetchone()[0]
            retry_inconsistent = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE (status IN ('pending', 'done') AND retry_after IS NOT NULL) OR (status = 'retry' AND retryable = 0)"
            ).fetchone()[0]
            message_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            message_fts_count = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
            transcript_count = conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]
            transcript_fts_count = conn.execute("SELECT COUNT(*) FROM transcripts_fts").fetchone()[0]
            orphan_metadata = conn.execute(
                """SELECT COUNT(*) FROM media m WHERE NOT EXISTS
                   (SELECT 1 FROM messages msg WHERE msg.chat_id = m.chat_id AND msg.message_id = m.message_id)"""
            ).fetchone()[0]
            downloaded_without_key = conn.execute(
                "SELECT COUNT(*) FROM media WHERE status = 'downloaded' AND storage_key IS NULL AND local_path IS NULL"
            ).fetchone()[0]
        return {
            "schema": {
                "status": compatibility,
                "current_version": current,
                "supported_version": SCHEMA_VERSION,
                "history": [{"version": row["version"], "checksum": row["checksum"], "applied_at": row["applied_at"]} for row in applied],
            },
            "integrity": {"status": "ok" if integrity == "ok" else "error"},
            "queue": {"status_counts": queue, "stale_processing": stale_processing, "missing_media_jobs": missing_media_jobs, "retry_inconsistent": retry_inconsistent},
            "indexes": {"status": "ok" if (message_count == message_fts_count and transcript_count == transcript_fts_count) else "stale", "messages": message_count, "message_fts": message_fts_count, "transcripts": transcript_count, "transcript_fts": transcript_fts_count},
            "orphaned_media_metadata": {"missing_message": orphan_metadata, "downloaded_without_storage_key": downloaded_without_key},
        }

    def list_jobs(
        self,
        *,
        stage: str | None = None,
        status: str | None = None,
        retryable: bool | None = None,
        chat_id: int | None = None,
        older_than: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("job limit must be between 1 and 500")
        if older_than is not None:
            older_than = _parse_aware_timestamp(older_than, "older_than").isoformat()
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (("stage", stage), ("status", status), ("chat_id", chat_id)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if retryable is not None:
            clauses.append("retryable = ?")
            params.append(int(retryable))
        if older_than is not None:
            clauses.append("updated_at < ?")
            params.append(older_than)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT id, stage, status, chat_id, message_id, media_id, error, retryable, retry_after, created_at, updated_at FROM jobs {where} ORDER BY updated_at DESC, id DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
        return [{**dict(row), "error": _safe_error(row["error"])} for row in rows]

    def retry_jobs(self, job_ids: list[int], *, override_retry_after: bool = False, actor: str = "human") -> dict[str, Any]:
        selected = sorted(set(job_ids))
        if not selected:
            raise ValueError("at least one job id is required")
        if len(selected) > 100:
            raise ValueError("retry batch is limited to 100 explicit job ids")
        changed = 0
        skipped: dict[str, int] = {}
        with self.connect() as conn:
            for job_id in selected:
                row = conn.execute("SELECT id, status, retryable, retry_after FROM jobs WHERE id = ?", (job_id,)).fetchone()
                reason = None
                if not row:
                    reason = "not_found"
                elif not row["retryable"]:
                    reason = "permanent_failure"
                elif row["status"] not in {"retry", "failed"}:
                    reason = "not_retryable_status"
                elif row["retry_after"]:
                    try:
                        retry_after = _parse_aware_timestamp(row["retry_after"], "retry_after")
                    except ValueError:
                        reason = "invalid_retry_after"
                    else:
                        if retry_after > datetime.now(UTC) and not override_retry_after:
                            reason = "backoff_active"
                if reason:
                    skipped[reason] = skipped.get(reason, 0) + 1
                    continue
                conn.execute("UPDATE jobs SET status = 'pending', error = NULL, retry_after = NULL, updated_at = ? WHERE id = ?", (now_iso(), job_id))
                changed += 1
            conn.execute(
                "INSERT INTO audit_events(event_type, scope, details_json, created_at) VALUES (?, ?, ?, ?)",
                ("jobs_retried", None, json.dumps({"actor": actor, "job_ids": selected, "changed": changed, "skipped": skipped}), now_iso()),
            )
        return {"selected": selected, "changed": changed, "skipped": skipped}

    def repair_jobs(self, *, apply: bool = False, stale_after_hours: int = 1, actor: str = "human") -> dict[str, Any]:
        if not 1 <= stale_after_hours <= 24 * 30:
            raise ValueError("stale_after_hours must be between 1 and 720")
        cutoff = (datetime.now(UTC) - timedelta(hours=stale_after_hours)).isoformat()
        findings: list[dict[str, Any]] = []
        with self.connect() as conn:
            stale = conn.execute("SELECT id FROM jobs WHERE status = 'processing' AND updated_at < ?", (cutoff,)).fetchall()
            findings.extend({"action": "requeue_stale_processing", "job_id": row["id"]} for row in stale)
            missing = conn.execute(
                """SELECT m.id, m.chat_id, m.message_id FROM media m WHERE m.status = 'pending'
                   AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.stage = 'media_download' AND j.media_id = m.id)"""
            ).fetchall()
            findings.extend({"action": "enqueue_missing_media_download", "media_id": row["id"], "chat_id": row["chat_id"], "message_id": row["message_id"]} for row in missing)
            inconsistent = conn.execute(
                "SELECT id, status FROM jobs WHERE (status IN ('pending', 'done') AND retry_after IS NOT NULL) OR (status = 'retry' AND retryable = 0)"
            ).fetchall()
            findings.extend({"action": "normalize_retry_metadata", "job_id": row["id"]} for row in inconsistent)
            retry_rows = conn.execute("SELECT id, retry_after FROM jobs WHERE retry_after IS NOT NULL").fetchall()
            inconsistent_ids = {item["job_id"] for item in findings if item["action"] == "normalize_retry_metadata"}
            for row in retry_rows:
                try:
                    _parse_aware_timestamp(row["retry_after"], "retry_after")
                except ValueError:
                    if row["id"] not in inconsistent_ids:
                        findings.append({"action": "normalize_invalid_retry_after", "job_id": row["id"]})
            if apply:
                for item in findings:
                    if item["action"] == "requeue_stale_processing":
                        conn.execute("UPDATE jobs SET status = 'retry', retryable = 1, retry_after = NULL, updated_at = ? WHERE id = ?", (now_iso(), item["job_id"]))
                    elif item["action"] == "enqueue_missing_media_download":
                        conn.execute("INSERT INTO jobs(stage, status, chat_id, message_id, media_id, payload_json, created_at, updated_at) VALUES ('media_download', 'pending', ?, ?, ?, '{}', ?, ?)", (item["chat_id"], item["message_id"], item["media_id"], now_iso(), now_iso()))
                    else:
                        conn.execute("UPDATE jobs SET status = CASE WHEN status = 'retry' THEN 'failed' ELSE status END, retry_after = NULL, updated_at = ? WHERE id = ?", (now_iso(), item["job_id"]))
                affected = {
                    "job_ids": [item["job_id"] for item in findings if "job_id" in item][:100],
                    "media_ids": [item["media_id"] for item in findings if "media_id" in item][:100],
                }
                conn.execute("INSERT INTO audit_events(event_type, scope, details_json, created_at) VALUES (?, ?, ?, ?)", ("jobs_repaired", None, json.dumps({"actor": actor, "applied": len(findings), "actions": _count_actions(findings), "affected": affected}), now_iso()))
        return {"dry_run": not apply, "proposed": findings, "applied": len(findings) if apply else 0, "counts": _count_actions(findings)}

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

    def upsert_message(self, message: MessageRecord) -> None:
        self.upsert_messages([message])

    def upsert_messages(self, messages: Sequence[MessageRecord]) -> None:
        """Write a page of messages in one transaction (one fsync, not one per message)."""

        if not messages:
            return
        with self.connect() as conn:
            titles: dict[int, str] = {}
            for message in messages:
                if message.chat_id not in titles:
                    titles[message.chat_id] = self._chat_title(conn, message.chat_id)
                self._upsert_message(conn, message, titles[message.chat_id])

    def _upsert_message(self, conn: sqlite3.Connection, message: MessageRecord, chat_title: str) -> None:
        row = conn.execute(
            """
            INSERT INTO messages(
                chat_id, message_id, date, text, sender_id, sender_name,
                reply_to_message_id, forward_from, edit_date, has_media, media_type,
                links_json, topic_id, updated_at
            )
            VALUES (
                :chat_id, :message_id, :date, :text, :sender_id, :sender_name,
                :reply_to_message_id, :forward_from, :edit_date, :has_media, :media_type,
                :links_json, :topic_id, :updated_at
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
                topic_id=COALESCE(excluded.topic_id, messages.topic_id),
                updated_at=excluded.updated_at
            RETURNING id
            """,
            {
                **asdict(message),
                "date": message.date.isoformat(),
                "edit_date": message.edit_date.isoformat() if message.edit_date else None,
                "has_media": int(message.has_media),
                "updated_at": now_iso(),
            },
        ).fetchone()
        if row:
            conn.execute("DELETE FROM messages_fts WHERE rowid = ?", (row["id"],))
            conn.execute(
                "INSERT INTO messages_fts(rowid, text, chat_title) VALUES (?, ?, ?)",
                (row["id"], message.text, chat_title),
            )

    def upsert_forum_topics(self, chat_id: int, topics: Sequence[tuple[int, str]]) -> None:
        if not topics:
            return
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO forum_topics(chat_id, topic_id, title, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, topic_id) DO UPDATE SET title=excluded.title, updated_at=excluded.updated_at
                """,
                [(chat_id, int(topic_id), str(title), now_iso()) for topic_id, title in topics],
            )

    def message_bounds(self, chat_id: int, topic_id: int | None = None) -> dict[str, Any] | None:
        """Oldest/newest stored message of a chat, or of one forum topic."""

        topic_sql = ""
        params: list[Any] = [chat_id]
        if topic_id is not None:
            topic_sql = " AND topic_id = ?"
            params.append(topic_id)
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS n, MIN(message_id) AS oldest_id, MAX(message_id) AS newest_id, "
                f"MIN(date) AS oldest_date, MAX(date) AS newest_date FROM messages WHERE chat_id = ?{topic_sql}",
                params,
            ).fetchone()
        if not row or not row["n"]:
            return None
        return {
            "count": row["n"],
            "oldest_id": row["oldest_id"],
            "newest_id": row["newest_id"],
            "oldest_date": row["oldest_date"],
            "newest_date": row["newest_date"],
        }

    def forum_topics(self, chat_ids: Sequence[int]) -> dict[int, dict[int, str]]:
        if not chat_ids:
            return {}
        marks = ",".join("?" for _ in chat_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT chat_id, topic_id, title FROM forum_topics WHERE chat_id IN ({marks})", list(chat_ids)
            ).fetchall()
        topics: dict[int, dict[int, str]] = {}
        for row in rows:
            topics.setdefault(row["chat_id"], {})[row["topic_id"]] = row["title"]
        return topics

    def update_sync_state(
        self,
        chat_id: int,
        newest_message_id: int | None = None,
        oldest_message_id: int | None = None,
        retry_after: str | None | object = _UNSET,
    ) -> None:
        with self.connect() as conn:
            current = conn.execute("SELECT * FROM sync_state WHERE chat_id = ?", (chat_id,)).fetchone()
            if current:
                newest = max(filter(None, [current["newest_message_id"], newest_message_id]), default=None)
                oldest_values = [value for value in [current["oldest_message_id"], oldest_message_id] if value]
                oldest = min(oldest_values) if oldest_values else None
                retry = current["retry_after"] if retry_after is _UNSET else retry_after
                conn.execute(
                    """
                    UPDATE sync_state
                    SET newest_message_id = ?, oldest_message_id = ?, last_synced_at = ?, retry_after = ?
                    WHERE chat_id = ?
                    """,
                    (newest, oldest, now_iso(), retry, chat_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO sync_state(chat_id, newest_message_id, oldest_message_id, last_synced_at, retry_after)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (chat_id, newest_message_id, oldest_message_id, now_iso(), None if retry_after is _UNSET else retry_after),
                )

    def get_sync_state(self, chat_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM sync_state WHERE chat_id = ?", (chat_id,)).fetchone()
        return dict(row) if row else None

    def enqueue_media(
        self,
        chat_id: int,
        message_id: int,
        media_type: str,
        telegram_file_id: str | None = None,
        transcription_policy: str = "auto",
    ) -> int:
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
            exists = conn.execute(
                """SELECT 1 FROM jobs
                   WHERE stage = 'media_download' AND media_id = ? AND status IN ('pending', 'retry', 'done')""",
                (media_id,),
            ).fetchone()
            if not exists:
                conn.execute(
                    """
                    INSERT INTO jobs(stage, status, chat_id, message_id, media_id, payload_json, created_at, updated_at)
                    VALUES ('media_download', 'pending', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chat_id,
                        message_id,
                        media_id,
                        json.dumps({"transcription_policy": transcription_policy}),
                        now_iso(),
                        now_iso(),
                    ),
                )
            return media_id

    def get_pending_jobs(self, stage: str, limit: int = 20, media_ids: set[int] | None = None) -> list[JobRecord]:
        media_filter = ""
        params: list[Any] = [stage, now_iso()]
        if media_ids:
            placeholders = ", ".join("?" for _ in media_ids)
            media_filter = f" AND media_id IN ({placeholders})"
            params.extend(sorted(media_ids))
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, stage, status, chat_id, message_id, media_id, payload_json
                FROM jobs
                WHERE stage = ? AND status IN ('pending', 'retry')
                  AND (retry_after IS NULL OR retry_after <= ?)
                  {media_filter}
                ORDER BY id
                LIMIT ?
                """,
                params,
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

    def requeue_media_download(self, media_id: int, transcription_policy: str = "auto") -> None:
        with self.connect() as conn:
            media = conn.execute("SELECT chat_id, message_id FROM media WHERE id = ?", (media_id,)).fetchone()
            if not media:
                raise ValueError(f"Unknown media id: {media_id}")
            exists = conn.execute(
                "SELECT 1 FROM jobs WHERE stage = 'media_download' AND media_id = ? AND status IN ('pending', 'retry')",
                (media_id,),
            ).fetchone()
            if not exists:
                conn.execute(
                    """
                    INSERT INTO jobs(stage, status, chat_id, message_id, media_id, payload_json, created_at, updated_at)
                    VALUES ('media_download', 'pending', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        media["chat_id"], media["message_id"], media_id,
                        json.dumps({"transcription_policy": transcription_policy}), now_iso(), now_iso(),
                    ),
                )

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
                storage_key=row["storage_key"],
                local_path=row["local_path"],
                status=row["status"],
            )

    def media_for_message(self, chat_id: int, message_id: int) -> list[MediaRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM media WHERE chat_id = ? AND message_id = ? ORDER BY id",
                (chat_id, message_id),
            ).fetchall()
        return [self._media_record(row) for row in rows]

    def update_media_downloaded(
        self,
        media_id: int,
        *,
        sha256: str,
        size_bytes: int,
        storage_key: str | None = None,
        local_path: str | None = None,
        mime_type: str | None = None,
        enqueue_transcription: bool = True,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE media
                SET storage_key = COALESCE(?, storage_key), local_path = COALESCE(?, local_path),
                    sha256 = ?, size_bytes = ?, mime_type = COALESCE(?, mime_type),
                    status = 'downloaded', updated_at = ?
                WHERE id = ?
                """,
                (storage_key, local_path, sha256, size_bytes, mime_type, now_iso(), media_id),
            )
            media = conn.execute("SELECT chat_id, message_id FROM media WHERE id = ?", (media_id,)).fetchone()
            exists = conn.execute(
                "SELECT 1 FROM jobs WHERE stage = 'transcription' AND media_id = ? AND status IN ('pending', 'retry')",
                (media_id,),
            ).fetchone()
            if media and enqueue_transcription and not exists:
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

    def find_media_by_sha256(self, digest: str) -> MediaRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM media WHERE sha256 = ? AND (storage_key IS NOT NULL OR local_path IS NOT NULL) LIMIT 1",
                (digest,),
            ).fetchone()
            if not row:
                return None
            return self._media_record(row)

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
            return transcript_id

    def rebuild_indexes(self) -> dict[str, int]:
        with self.connect() as conn:
            conn.execute("DELETE FROM messages_fts")
            conn.execute("DELETE FROM transcripts_fts")
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
            transcripts = conn.execute(
                """
                SELECT t.id, t.text, media.chat_id, media.message_id
                FROM transcripts t
                JOIN media ON media.id = t.media_id
                """
            ).fetchall()
            for row in transcripts:
                conn.execute("INSERT INTO transcripts_fts(rowid, text) VALUES (?, ?)", (row["id"], row["text"]))
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
                "sync_state",
                "chats",
                "audit_events",
            ]:
                conn.execute(f"DELETE FROM {table}")

    def export_messages(self, filters: SearchFilters, limit: int = 10000) -> list[dict[str, Any]]:
        where, params = _message_filter_sql("m", filters.normalized())
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT m.chat_id, m.message_id, m.date, m.text, m.sender_id, m.sender_name,
                       m.reply_to_message_id, m.forward_from, m.edit_date, m.has_media, m.media_type,
                       m.links_json, c.title AS chat_title
                FROM messages m
                LEFT JOIN chats c ON c.chat_id = m.chat_id
                WHERE 1 = 1 {where}
                ORDER BY m.chat_id, m.message_id
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        return [dict(row) | {"citation": f"tg://chat/{row['chat_id']}/message/{row['message_id']}"} for row in rows]

    def _chat_title(self, conn: sqlite3.Connection, chat_id: int) -> str:
        row = conn.execute("SELECT title FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
        return row["title"] if row else ""

    @staticmethod
    def _media_record(row: sqlite3.Row) -> MediaRecord:
        return MediaRecord(
            id=row["id"],
            chat_id=row["chat_id"],
            message_id=row["message_id"],
            telegram_file_id=row["telegram_file_id"],
            media_type=row["media_type"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
            storage_key=row["storage_key"],
            local_path=row["local_path"],
            status=row["status"],
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


def _safe_error(value: str | None) -> str | None:
    if not value:
        return None
    # Job inspection never returns arbitrary provider text: it can contain
    # access tokens, local paths, or a quoted message/transcript.
    normalized = value.lower()
    if any(token in normalized for token in ("flood", "rate limit", "timeout", "temporar", "connection")):
        return "transient_provider_error"
    if any(token in normalized for token in ("auth", "permission", "forbidden", "unauthorized")):
        return "authorization_error"
    if any(token in normalized for token in ("not found", "missing", "filenotfound")):
        return "missing_resource"
    return "operation_failed"


def _count_actions(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        action = finding["action"]
        counts[action] = counts.get(action, 0) + 1
    return counts


def _parse_aware_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp with timezone") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be an ISO-8601 timestamp with timezone")
    return parsed.astimezone(UTC)


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
    if filters.chat_ids is not None:
        if not filters.chat_ids:
            parts.append("1 = 0")
        else:
            parts.append(f"{chat_alias}.chat_id IN ({', '.join('?' for _ in filters.chat_ids)})")
            params.extend(filters.chat_ids)
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
    if filters.media_types is not None:
        if not filters.media_types:
            parts.append("1 = 0")
        else:
            placeholders = ", ".join("?" for _ in filters.media_types)
            parts.append(f"{chat_alias}.media_type IN ({placeholders})" if media_alias else f"{message_alias}.media_type IN ({placeholders})")
            params.extend(filters.media_types)
    if filters.has_link is not None:
        parts.append(f"{message_alias}.links_json != '[]'" if filters.has_link else f"{message_alias}.links_json = '[]'")
    return (f" AND {' AND '.join(parts)}" if parts else "", params)
