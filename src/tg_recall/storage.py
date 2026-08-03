from __future__ import annotations

import json
import sqlite3
import re
import hashlib
import importlib.util
import math
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from .context_budgeting import ActualUsage, RetrievalStage
from .hybrid_retrieval import (
    EmbeddedVector,
    EmbeddingBatch,
    EmbeddingModelMetadata,
    EmbeddingProvider,
    EmbeddingRecord,
    IndexCheckpoint,
    RetrievalCandidate,
    RetrievalMode,
    SourceType,
    StoredVectorMetadata,
    embed_batch,
    plan_embedding_batches,
)
from .models import ChatRecord, JobRecord, MediaRecord, MessageRecord, SearchFilters, SearchResult
from .knowledge_catalog import EvidenceSetReference, KnowledgeCatalogError, KnowledgeScope, ResearchCheckpoint, ResearchSession, validate_session_metadata
from .security import harden_path


_UNSET = object()


SCHEMA_VERSION = 6
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

                CREATE TABLE IF NOT EXISTS sync_scopes (
                    name TEXT PRIMARY KEY,
                    since TEXT,
                    until TEXT,
                    media_policy TEXT NOT NULL DEFAULT 'none',
                    transcription_policy TEXT NOT NULL DEFAULT 'off',
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
            self._ensure_column(conn, "schema_migrations", "checksum", "TEXT")
            self._ensure_column(conn, "sync_scopes", "media_policy", "TEXT NOT NULL DEFAULT 'none'")
            self._ensure_column(conn, "sync_scopes", "transcription_policy", "TEXT NOT NULL DEFAULT 'off'")
            self._ensure_column(conn, "media", "storage_key", "TEXT")
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
        harden_path(self.path, is_dir=False)

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
            semantic_count = conn.execute("SELECT COUNT(*) FROM semantic_index").fetchone()[0]
            orphan_metadata = conn.execute(
                """SELECT COUNT(*) FROM media m WHERE NOT EXISTS
                   (SELECT 1 FROM messages msg WHERE msg.chat_id = m.chat_id AND msg.message_id = m.message_id)"""
            ).fetchone()[0]
            downloaded_without_key = conn.execute(
                "SELECT COUNT(*) FROM media WHERE status = 'downloaded' AND storage_key IS NULL AND local_path IS NULL"
            ).fetchone()[0]
            embedding_index = self._embedding_index_status_conn(conn)
        return {
            "schema": {
                "status": compatibility,
                "current_version": current,
                "supported_version": SCHEMA_VERSION,
                "history": [{"version": row["version"], "checksum": row["checksum"], "applied_at": row["applied_at"]} for row in applied],
            },
            "integrity": {"status": "ok" if integrity == "ok" else "error"},
            "queue": {"status_counts": queue, "stale_processing": stale_processing, "missing_media_jobs": missing_media_jobs, "retry_inconsistent": retry_inconsistent},
            "indexes": {"status": "ok" if (message_count == message_fts_count and transcript_count == transcript_fts_count and semantic_count >= message_count + transcript_count) else "stale", "messages": message_count, "message_fts": message_fts_count, "transcripts": transcript_count, "transcript_fts": transcript_fts_count, "semantic": semantic_count},
            "orphaned_media_metadata": {"missing_message": orphan_metadata, "downloaded_without_storage_key": downloaded_without_key},
            "embedding_runtime": _embedding_runtime_diagnostics(),
            "embedding_index": embedding_index,
        }

    def embedding_records(self, filters: SearchFilters | None = None) -> list[EmbeddingRecord]:
        """Return the local, profile-scoped sources eligible for embedding.

        The database belongs to one profile; callers must still pass the same
        already-authorized ``SearchFilters`` used by their FTS query.  Text is
        returned only to the explicit local embedding provider and is never
        recorded in vector metadata.
        """

        with self.connect() as conn:
            return self._embedding_records_conn(conn, (filters or SearchFilters()).normalized())

    def stored_vector_metadata(self, model: EmbeddingModelMetadata | None = None) -> list[StoredVectorMetadata]:
        """Return metadata only, without serialized vectors or archive text."""

        with self.connect() as conn:
            where = "WHERE v.model_identity = ?" if model else ""
            params: tuple[str, ...] = (model.identity,) if model else ()
            rows = conn.execute(
                f"""
                SELECT v.source_type, v.source_id, v.source_hash, v.indexed_at,
                       em.provider, em.model_id, em.dimensions, em.model_source_hash
                FROM embedding_vectors v
                JOIN embedding_models em ON em.model_identity = v.model_identity
                {where}
                ORDER BY v.source_type, v.source_id
                """,
                params,
            ).fetchall()
        return [
            StoredVectorMetadata(
                SourceType(row["source_type"]),
                row["source_id"],
                row["source_hash"],
                EmbeddingModelMetadata(row["provider"], row["model_id"], row["dimensions"], row["model_source_hash"]),
                _parse_aware_timestamp(row["indexed_at"], "embedding indexed_at"),
            )
            for row in rows
        ]

    def embedding_index_status(
        self,
        model: EmbeddingModelMetadata | None = None,
        filters: SearchFilters | None = None,
    ) -> dict[str, Any]:
        """Report index freshness without exposing messages, vectors, or paths."""

        with self.connect() as conn:
            records = self._embedding_records_conn(conn, (filters or SearchFilters()).normalized())
            return self._embedding_index_status_conn(conn, model=model, records=records)

    def build_embedding_index(
        self,
        provider: EmbeddingProvider,
        *,
        filters: SearchFilters | None = None,
        batch_size: int = 32,
        max_batches: int | None = None,
        rebuild: bool = False,
    ) -> dict[str, Any]:
        """Embed bounded local batches and commit each completed batch atomically.

        ``provider`` is supplied by the caller after explicit local model
        configuration.  This method never constructs a provider, imports an
        optional runtime, downloads a model, or makes network requests.
        """

        if batch_size < 1:
            raise ValueError("embedding batch_size must be positive")
        if max_batches is not None and max_batches < 1:
            raise ValueError("embedding max_batches must be positive")
        model = provider.metadata
        normalized_filters = (filters or SearchFilters()).normalized()
        with self.connect() as conn:
            records = self._embedding_records_conn(conn, normalized_filters)
            stored = self._stored_vector_metadata_conn(conn, model)
            checkpoint = self._embedding_checkpoint_conn(conn, model)
        if rebuild:
            batches = _fixed_embedding_batches(records, batch_size)
        else:
            batches = plan_embedding_batches(records, model, checkpoint, stored, batch_size=batch_size)
        if max_batches is not None:
            batches = batches[:max_batches]

        vectors_written = 0
        for batch in batches:
            embedded = embed_batch(provider, batch)
            self._persist_embedding_batch(embedded, model)
            vectors_written += len(embedded)
        status = self.embedding_index_status(model, normalized_filters)
        return {
            "model": _embedding_model_public(model),
            "batches_committed": len(batches),
            "vectors_written": vectors_written,
            "remaining_records": status["missing"] + status["stale"],
            "index": status,
        }

    def rebuild_embedding_index(
        self,
        provider: EmbeddingProvider,
        *,
        filters: SearchFilters | None = None,
        batch_size: int = 32,
        max_batches: int | None = None,
    ) -> dict[str, Any]:
        """Explicitly recompute selected local sources without deleting others."""

        return self.build_embedding_index(
            provider,
            filters=filters,
            batch_size=batch_size,
            max_batches=max_batches,
            rebuild=True,
        )

    def remove_embedding_index(self, *, model_identity: str | None = None, all_models: bool = False) -> dict[str, int | str]:
        """Explicitly remove serialized vectors; source messages stay untouched."""

        if all_models == (model_identity is not None):
            raise ValueError("choose exactly one of model_identity or all_models")
        with self.connect() as conn:
            if all_models:
                deleted = conn.execute("DELETE FROM embedding_models").rowcount
                scope = "all"
            else:
                deleted = conn.execute("DELETE FROM embedding_models WHERE model_identity = ?", (model_identity,)).rowcount
                scope = "model"
            conn.execute(
                "INSERT INTO audit_events(event_type, scope, details_json, created_at) VALUES (?, ?, ?, ?)",
                ("embedding_index_removed", scope, json.dumps({"models_deleted": deleted}), now_iso()),
            )
        return {"scope": scope, "models_deleted": deleted}

    def vector_candidates(
        self,
        query_vector: tuple[float, ...],
        model: EmbeddingModelMetadata,
        *,
        filters: SearchFilters | None = None,
        limit: int = 20,
    ) -> list[RetrievalCandidate]:
        """Return genuine, current vector candidates after the shared filters.

        This storage primitive intentionally accepts an already-computed local
        query vector.  It cannot turn token-overlap results into semantic
        candidates and it excludes stale source hashes before scoring.
        """

        if limit < 1:
            return []
        _validate_embedding_values(query_vector, model.dimensions)
        query_norm = math.sqrt(sum(value * value for value in query_vector))
        if query_norm == 0:
            raise ValueError("embedding query vector must not be zero")
        with self.connect() as conn:
            records = self._embedding_records_conn(conn, (filters or SearchFilters()).normalized())
            rows = conn.execute(
                "SELECT source_type, source_id, source_hash, vector_json FROM embedding_vectors WHERE model_identity = ?",
                (model.identity,),
            ).fetchall()
        records_by_key = {(record.source_type.value, record.source_id): record for record in records}
        scored: list[tuple[float, EmbeddingRecord]] = []
        for row in rows:
            record = records_by_key.get((row["source_type"], row["source_id"]))
            if record is None or record.source_hash != row["source_hash"]:
                continue
            values = _decode_embedding_values(row["vector_json"], model.dimensions)
            norm = math.sqrt(sum(value * value for value in values))
            if norm == 0:
                continue
            score = sum(left * right for left, right in zip(query_vector, values, strict=True)) / (query_norm * norm)
            scored.append((score, record))
        scored.sort(key=lambda item: (-item[0], item[1].source_type.value, item[1].chat_id, item[1].message_id, item[1].source_id))
        return [
            RetrievalCandidate(
                citation=f"tg://chat/{record.chat_id}/message/{record.message_id}",
                chat_id=record.chat_id,
                message_id=record.message_id,
                text=record.text,
                source_type=record.source_type,
                source_id=record.source_id,
                score=score,
                channel=RetrievalMode.SEMANTIC,
                transcript_id=record.source_id if record.source_type == SourceType.TRANSCRIPT else None,
            )
            for score, record in scored[:limit]
        ]

    def _embedding_records_conn(self, conn: sqlite3.Connection, filters: SearchFilters) -> list[EmbeddingRecord]:
        message_where, message_params = _message_filter_sql("m", filters)
        message_rows = conn.execute(
            f"""
            SELECT m.id AS source_id, m.chat_id, m.message_id, m.text
            FROM messages m
            WHERE m.text != ''{message_where}
            """,
            message_params,
        ).fetchall()
        transcript_where, transcript_params = _message_filter_sql("m", filters, media_alias="media")
        transcript_rows = conn.execute(
            f"""
            SELECT t.id AS source_id, media.chat_id, media.message_id, t.text
            FROM transcripts t
            JOIN media ON media.id = t.media_id
            LEFT JOIN messages m ON m.chat_id = media.chat_id AND m.message_id = media.message_id
            WHERE t.text != ''{transcript_where}
            """,
            transcript_params,
        ).fetchall()
        records = [
            *(EmbeddingRecord.from_text(SourceType.MESSAGE, row["source_id"], row["chat_id"], row["message_id"], row["text"]) for row in message_rows),
            *(EmbeddingRecord.from_text(SourceType.TRANSCRIPT, row["source_id"], row["chat_id"], row["message_id"], row["text"]) for row in transcript_rows),
        ]
        return sorted(records, key=lambda record: (record.source_type.value, record.chat_id, record.message_id, record.source_id))

    def _stored_vector_metadata_conn(self, conn: sqlite3.Connection, model: EmbeddingModelMetadata) -> list[StoredVectorMetadata]:
        rows = conn.execute(
            """
            SELECT v.source_type, v.source_id, v.source_hash, v.indexed_at,
                   em.provider, em.model_id, em.dimensions, em.model_source_hash
            FROM embedding_vectors v
            JOIN embedding_models em ON em.model_identity = v.model_identity
            WHERE v.model_identity = ?
            ORDER BY source_type, source_id
            """,
            (model.identity,),
        ).fetchall()
        return [
            StoredVectorMetadata(
                SourceType(row["source_type"]),
                row["source_id"],
                row["source_hash"],
                EmbeddingModelMetadata(row["provider"], row["model_id"], row["dimensions"], row["model_source_hash"]),
                _parse_aware_timestamp(row["indexed_at"], "embedding indexed_at"),
            )
            for row in rows
        ]

    @staticmethod
    def _embedding_checkpoint_conn(conn: sqlite3.Connection, model: EmbeddingModelMetadata) -> IndexCheckpoint | None:
        row = conn.execute(
            "SELECT last_source_type, last_source_id, last_source_hash, updated_at FROM embedding_index_checkpoints WHERE model_identity = ?",
            (model.identity,),
        ).fetchone()
        if row is None or row["last_source_type"] is None:
            return None
        return IndexCheckpoint(
            model.identity,
            ((SourceType(row["last_source_type"]), row["last_source_id"], row["last_source_hash"]),),
            _parse_aware_timestamp(row["updated_at"], "embedding checkpoint updated_at"),
        )

    def _persist_embedding_batch(self, embedded: list[EmbeddedVector], model: EmbeddingModelMetadata) -> None:
        if not embedded:
            return
        if any(item.model != model for item in embedded):
            raise ValueError("embedding batch model metadata is inconsistent")
        last = embedded[-1].record
        with self.connect() as conn:
            now = now_iso()
            existing = conn.execute(
                "SELECT provider, model_id, dimensions, model_source_hash FROM embedding_models WHERE model_identity = ?",
                (model.identity,),
            ).fetchone()
            if existing is not None and (
                existing["provider"], existing["model_id"], existing["dimensions"], existing["model_source_hash"]
            ) != (model.provider, model.model_id, model.dimensions, model.model_source_hash):
                raise ValueError("embedding model identity is incompatible; remove the existing index first")
            conn.execute(
                """
                INSERT INTO embedding_models(model_identity, provider, model_id, dimensions, model_source_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(model_identity) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (model.identity, model.provider, model.model_id, model.dimensions, model.model_source_hash, now, now),
            )
            for item in embedded:
                _validate_embedding_values(item.values, model.dimensions)
                record = item.record
                conn.execute(
                    """
                    INSERT INTO embedding_vectors(
                        source_type, source_id, model_identity, chat_id, message_id,
                        source_hash, vector_encoding, vector_json, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'json-float-v1', ?, ?)
                    ON CONFLICT(source_type, source_id, model_identity) DO UPDATE SET
                        chat_id=excluded.chat_id, message_id=excluded.message_id,
                        source_hash=excluded.source_hash, vector_encoding=excluded.vector_encoding,
                        vector_json=excluded.vector_json, indexed_at=excluded.indexed_at
                    """,
                    (
                        record.source_type.value,
                        record.source_id,
                        model.identity,
                        record.chat_id,
                        record.message_id,
                        record.source_hash,
                        json.dumps(item.values, separators=(",", ":")),
                        now,
                    ),
                )
            conn.execute(
                """
                INSERT INTO embedding_index_checkpoints(
                    model_identity, completed_batches, last_source_type, last_source_id, last_source_hash, updated_at
                ) VALUES (?, 1, ?, ?, ?, ?)
                ON CONFLICT(model_identity) DO UPDATE SET
                    completed_batches=embedding_index_checkpoints.completed_batches + 1,
                    last_source_type=excluded.last_source_type, last_source_id=excluded.last_source_id,
                    last_source_hash=excluded.last_source_hash, updated_at=excluded.updated_at
                """,
                (model.identity, last.source_type.value, last.source_id, last.source_hash, now),
            )

    def _embedding_index_status_conn(
        self,
        conn: sqlite3.Connection,
        model: EmbeddingModelMetadata | None = None,
        records: list[EmbeddingRecord] | None = None,
    ) -> dict[str, Any]:
        model_rows = conn.execute(
            "SELECT model_identity, provider, model_id, dimensions, model_source_hash, updated_at FROM embedding_models ORDER BY model_identity"
        ).fetchall()
        stored_model_count = len(model_rows)
        if model is None:
            vector_count = conn.execute("SELECT COUNT(*) FROM embedding_vectors").fetchone()[0]
            checkpoint_count = conn.execute("SELECT COUNT(*) FROM embedding_index_checkpoints").fetchone()[0]
            return {
                "status": "not_configured" if not stored_model_count else "model_not_selected",
                "stored_models": stored_model_count,
                "vectors": vector_count,
                "checkpoints": checkpoint_count,
                "models": [
                    {"provider": row["provider"], "model_id": row["model_id"], "dimensions": row["dimensions"], "model_source_hash": row["model_source_hash"], "updated_at": row["updated_at"]}
                    for row in model_rows
                ],
            }
        if records is None:
            records = self._embedding_records_conn(conn, SearchFilters())
        selected_model = next((row for row in model_rows if row["model_identity"] == model.identity), None)
        incompatible_models = [
            row
            for row in model_rows
            if (row["provider"], row["model_id"], row["dimensions"], row["model_source_hash"])
            != (model.provider, model.model_id, model.dimensions, model.model_source_hash)
        ]
        vector_rows = conn.execute(
            "SELECT source_type, source_id, source_hash FROM embedding_vectors WHERE model_identity = ?",
            (model.identity,),
        ).fetchall()
        stored = {(row["source_type"], row["source_id"]): row["source_hash"] for row in vector_rows}
        current = missing = stale = 0
        for record in records:
            stored_hash = stored.get((record.source_type.value, record.source_id))
            if stored_hash is None:
                missing += 1
            elif stored_hash != record.source_hash:
                stale += 1
            else:
                current += 1
        checkpoint = conn.execute(
            "SELECT completed_batches, updated_at FROM embedding_index_checkpoints WHERE model_identity = ?",
            (model.identity,),
        ).fetchone()
        other_models = sum(row["model_identity"] != model.identity for row in model_rows)
        status = "incompatible" if selected_model is None and incompatible_models else ("current" if not missing and not stale else "stale")
        return {
            "status": status,
            "model": _embedding_model_public(model),
            "sources": len(records),
            "current": current,
            "missing": missing,
            "stale": stale,
            "vectors": len(vector_rows),
            "other_models": other_models,
            "incompatible_models": len(incompatible_models),
            "checkpoint": None if checkpoint is None else {"completed_batches": checkpoint["completed_batches"], "updated_at": checkpoint["updated_at"]},
        }

    def record_evidence_set(
        self,
        *,
        profile_id: str,
        scope_id: str,
        evidence_set: EvidenceSetReference,
        source_versions: dict[str, str],
    ) -> dict[str, Any]:
        """Persist one immutable, cited navigation set without raw archive text."""

        if not isinstance(evidence_set, EvidenceSetReference):
            raise ValueError("evidence set must use the immutable evidence contract")
        _validate_profile_scope(profile_id, scope_id, evidence_set.scope)
        _validate_compact_derived_text(evidence_set.purpose, "evidence purpose")
        _validate_compact_derived_text(evidence_set.query, "evidence query")
        _validate_compact_derived_text(evidence_set.summary, "evidence summary")
        sources = _validated_evidence_source_versions(evidence_set, source_versions)
        with self.connect() as conn:
            self._ensure_knowledge_profile_conn(conn, profile_id)
            try:
                conn.execute(
                    """
                    INSERT INTO evidence_sets(
                        profile_id, evidence_set_id, scope_id, scope_chat_ids_json,
                        purpose, query, summary, revision, topics_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile_id,
                        evidence_set.evidence_set_id,
                        scope_id,
                        _canonical_json(list(evidence_set.scope.chat_ids)),
                        evidence_set.purpose,
                        evidence_set.query,
                        evidence_set.summary,
                        evidence_set.revision,
                        _canonical_json(list(evidence_set.topics)),
                        evidence_set.created_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("evidence set is immutable and already exists for this profile") from exc
            for member in evidence_set.members:
                conn.execute(
                    """
                    INSERT INTO evidence_set_members(
                        profile_id, evidence_set_id, member_id, member_kind, logical_id, member_version
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile_id,
                        evidence_set.evidence_set_id,
                        member.member_id,
                        member.kind.value,
                        member.logical_id,
                        member.version,
                    ),
                )
                for citation in member.citations:
                    chat_id, message_id = _parse_tg_citation(citation)
                    conn.execute(
                        """
                        INSERT INTO evidence_member_sources(
                            profile_id, evidence_set_id, member_id, citation, chat_id, message_id, source_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            profile_id,
                            evidence_set.evidence_set_id,
                            member.member_id,
                            citation,
                            chat_id,
                            message_id,
                            sources[citation],
                        ),
                    )
        return self.evidence_set_view(profile_id=profile_id, evidence_set_id=evidence_set.evidence_set_id)

    def evidence_set_view(self, *, profile_id: str, evidence_set_id: str) -> dict[str, Any]:
        """Return stable references/versions only; never materialize source text."""

        _validate_knowledge_identifier(profile_id, "knowledge profile")
        _validate_knowledge_identifier(evidence_set_id, "evidence set")
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM evidence_sets WHERE profile_id = ? AND evidence_set_id = ?",
                (profile_id, evidence_set_id),
            ).fetchone()
            if row is None:
                raise KeyError("evidence set is not available in this profile")
            members = conn.execute(
                """
                SELECT member_id, member_kind, logical_id, member_version
                FROM evidence_set_members
                WHERE profile_id = ? AND evidence_set_id = ?
                ORDER BY member_id
                """,
                (profile_id, evidence_set_id),
            ).fetchall()
            sources = conn.execute(
                """
                SELECT member_id, citation, source_version
                FROM evidence_member_sources
                WHERE profile_id = ? AND evidence_set_id = ?
                ORDER BY member_id, citation
                """,
                (profile_id, evidence_set_id),
            ).fetchall()
        sources_by_member: dict[str, list[dict[str, str]]] = {}
        for source in sources:
            sources_by_member.setdefault(source["member_id"], []).append(
                {"citation": source["citation"], "source_version": source["source_version"]}
            )
        return {
            "created_at": row["created_at"],
            "evidence_set_id": row["evidence_set_id"],
            "members": [
                {
                    "kind": member["member_kind"],
                    "logical_id": member["logical_id"],
                    "member_id": member["member_id"],
                    "sources": sources_by_member.get(member["member_id"], []),
                    "version": member["member_version"],
                }
                for member in members
            ],
            "profile_id": row["profile_id"],
            "purpose": row["purpose"],
            "query": row["query"],
            "revision": row["revision"],
            "scope": {"chat_ids": json.loads(row["scope_chat_ids_json"]), "scope_id": row["scope_id"]},
            "summary": row["summary"],
            "topics": json.loads(row["topics_json"]),
        }

    def list_evidence_sets(
        self,
        *,
        profile_id: str,
        scope_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        _validate_knowledge_identifier(profile_id, "knowledge profile")
        if scope_id is not None:
            _validate_knowledge_identifier(scope_id, "knowledge scope")
        _validate_bounded_limit(limit, "evidence set limit", maximum=200)
        where = "WHERE profile_id = ?" + (" AND scope_id = ?" if scope_id is not None else "")
        params: list[Any] = [profile_id]
        if scope_id is not None:
            params.append(scope_id)
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT evidence_set_id FROM evidence_sets {where}
                ORDER BY created_at DESC, evidence_set_id
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self.evidence_set_view(profile_id=profile_id, evidence_set_id=row["evidence_set_id"]) for row in rows]

    def create_research_session(
        self,
        *,
        profile_id: str,
        scope_id: str,
        session: ResearchSession,
    ) -> dict[str, Any]:
        """Create compact resumable state; full prompts/reasoning have no storage path."""

        _validate_profile_scope(profile_id, scope_id, session.scope)
        _validate_research_session(session)
        with self.connect() as conn:
            self._ensure_knowledge_profile_conn(conn, profile_id)
            self._assert_checkpoint_evidence_sets_conn(conn, profile_id, scope_id, session.checkpoint)
            try:
                conn.execute(
                    """
                    INSERT INTO research_sessions(
                        profile_id, session_id, scope_id, scope_chat_ids_json,
                        purpose, budgets_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile_id,
                        session.session_id,
                        scope_id,
                        _canonical_json(list(session.scope.chat_ids)),
                        session.purpose,
                        _canonical_json(dict(session.budgets)),
                        session.created_at,
                        session.updated_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("research session already exists for this profile") from exc
            if not (session.created_at <= session.checkpoint.created_at <= session.updated_at):
                raise ValueError("research checkpoint timestamp must be within the session lifetime")
            self._insert_checkpoint_conn(conn, profile_id, session.session_id, session.checkpoint)
        return self.research_session_view(profile_id=profile_id, session_id=session.session_id)

    def append_research_checkpoint(
        self,
        *,
        profile_id: str,
        session_id: str,
        checkpoint: ResearchCheckpoint,
    ) -> dict[str, Any]:
        """Append an immutable compact checkpoint with monotonic timestamps."""

        _validate_knowledge_identifier(profile_id, "knowledge profile")
        _validate_knowledge_identifier(session_id, "research session")
        _validate_research_checkpoint(checkpoint)
        with self.connect() as conn:
            session = conn.execute(
                "SELECT created_at, updated_at, scope_id FROM research_sessions WHERE profile_id = ? AND session_id = ?",
                (profile_id, session_id),
            ).fetchone()
            if session is None:
                raise KeyError("research session is not available in this profile")
            if checkpoint.created_at <= session["updated_at"] or checkpoint.created_at < session["created_at"]:
                raise ValueError("research session checkpoints must have a monotonic timestamp")
            self._assert_checkpoint_evidence_sets_conn(conn, profile_id, session["scope_id"], checkpoint)
            self._insert_checkpoint_conn(conn, profile_id, session_id, checkpoint)
            conn.execute(
                "UPDATE research_sessions SET updated_at = ? WHERE profile_id = ? AND session_id = ?",
                (checkpoint.created_at, profile_id, session_id),
            )
        return self.research_session_view(profile_id=profile_id, session_id=session_id)

    def research_session_view(self, *, profile_id: str, session_id: str) -> dict[str, Any]:
        _validate_knowledge_identifier(profile_id, "knowledge profile")
        _validate_knowledge_identifier(session_id, "research session")
        with self.connect() as conn:
            session = conn.execute(
                "SELECT * FROM research_sessions WHERE profile_id = ? AND session_id = ?",
                (profile_id, session_id),
            ).fetchone()
            if session is None:
                raise KeyError("research session is not available in this profile")
            checkpoints = conn.execute(
                """
                SELECT checkpoint_at, summary, decisions_json, unresolved_questions_json, evidence_set_ids_json
                FROM research_session_checkpoints
                WHERE profile_id = ? AND session_id = ?
                ORDER BY checkpoint_at DESC
                """,
                (profile_id, session_id),
            ).fetchall()
        checkpoint_views = [
            {
                "created_at": row["checkpoint_at"],
                "decisions": json.loads(row["decisions_json"]),
                "evidence_set_ids": json.loads(row["evidence_set_ids_json"]),
                "summary": row["summary"],
                "unresolved_questions": json.loads(row["unresolved_questions_json"]),
            }
            for row in checkpoints
        ]
        return {
            "budgets": json.loads(session["budgets_json"]),
            "checkpoint": checkpoint_views[0],
            "checkpoint_count": len(checkpoint_views),
            "created_at": session["created_at"],
            "profile_id": session["profile_id"],
            "purpose": session["purpose"],
            "scope": {"chat_ids": json.loads(session["scope_chat_ids_json"]), "scope_id": session["scope_id"]},
            "session_id": session["session_id"],
            "updated_at": session["updated_at"],
        }

    def list_research_sessions(
        self,
        *,
        profile_id: str,
        scope_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        _validate_knowledge_identifier(profile_id, "knowledge profile")
        if scope_id is not None:
            _validate_knowledge_identifier(scope_id, "knowledge scope")
        _validate_bounded_limit(limit, "research session limit", maximum=200)
        where = "WHERE profile_id = ?" + (" AND scope_id = ?" if scope_id is not None else "")
        params: list[Any] = [profile_id]
        if scope_id is not None:
            params.append(scope_id)
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT session_id FROM research_sessions {where} ORDER BY updated_at DESC, session_id LIMIT ?",
                params,
            ).fetchall()
        return [self.research_session_view(profile_id=profile_id, session_id=row["session_id"]) for row in rows]

    def record_research_telemetry(
        self,
        *,
        profile_id: str,
        session_id: str,
        stage: RetrievalStage | str,
        retrieval_calls: int,
        returned_items: int,
        deduplicated_items: int,
        retries: int,
        latency_ms: int,
        estimated_tokens: int,
        counter: str,
        counter_version: str,
        safety_margin: float,
        reused_evidence: bool,
        sufficient: bool,
        actual_usage: ActualUsage | None = None,
    ) -> dict[str, Any]:
        """Store minimal session-local metrics, not prompts or host transcripts."""

        _validate_knowledge_identifier(profile_id, "knowledge profile")
        _validate_knowledge_identifier(session_id, "research session")
        stage_value = _validated_telemetry_stage(stage)
        values = (retrieval_calls, returned_items, deduplicated_items, retries, latency_ms, estimated_tokens)
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
            raise ValueError("research telemetry counts must be non-negative integers")
        if not isinstance(safety_margin, (int, float)) or isinstance(safety_margin, bool) or not 0 <= safety_margin < 1:
            raise ValueError("research telemetry safety_margin must be in [0, 1)")
        if not isinstance(reused_evidence, bool) or not isinstance(sufficient, bool):
            raise ValueError("research telemetry reused_evidence and sufficient must be booleans")
        _validate_safe_telemetry_label(counter, "counter")
        _validate_safe_telemetry_label(counter_version, "counter version")
        if actual_usage is not None:
            if not isinstance(actual_usage, ActualUsage):
                raise ValueError("actual usage must use the provider usage contract")
            _validate_safe_telemetry_label(actual_usage.source, "actual usage source")
            _validate_safe_telemetry_label(actual_usage.model, "actual usage model")
        with self.connect() as conn:
            if conn.execute(
                "SELECT 1 FROM research_sessions WHERE profile_id = ? AND session_id = ?",
                (profile_id, session_id),
            ).fetchone() is None:
                raise KeyError("research session is not available in this profile")
            now = now_iso()
            telemetry_id = conn.execute(
                """
                INSERT INTO research_session_telemetry(
                    profile_id, session_id, stage, retrieval_calls, returned_items,
                    deduplicated_items, retries, latency_ms, estimated_tokens,
                    counter, counter_version, safety_margin, reused_evidence,
                    sufficient, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile_id, session_id, stage_value, retrieval_calls, returned_items,
                    deduplicated_items, retries, latency_ms, estimated_tokens,
                    counter, counter_version, float(safety_margin), int(reused_evidence),
                    int(sufficient), now,
                ),
            ).lastrowid
            if actual_usage is not None:
                conn.execute(
                    """
                    INSERT INTO research_session_actual_usage(
                        telemetry_id, usage_source, model, input_tokens, cached_input_tokens,
                        output_tokens, reasoning_tokens, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        telemetry_id, actual_usage.source, actual_usage.model,
                        actual_usage.input_tokens, actual_usage.cached_input_tokens,
                        actual_usage.output_tokens, actual_usage.reasoning_tokens, now,
                    ),
                )
            self._trim_research_telemetry_conn(conn, profile_id, session_id)
        return self.research_telemetry_view(profile_id=profile_id, telemetry_id=int(telemetry_id))

    def research_telemetry_view(self, *, profile_id: str, telemetry_id: int) -> dict[str, Any]:
        _validate_knowledge_identifier(profile_id, "knowledge profile")
        if not isinstance(telemetry_id, int) or isinstance(telemetry_id, bool) or telemetry_id < 1:
            raise ValueError("telemetry_id must be a positive integer")
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT t.*, u.usage_source, u.model, u.input_tokens, u.cached_input_tokens,
                       u.output_tokens, u.reasoning_tokens, u.recorded_at AS usage_recorded_at
                FROM research_session_telemetry t
                LEFT JOIN research_session_actual_usage u ON u.telemetry_id = t.telemetry_id
                WHERE t.profile_id = ? AND t.telemetry_id = ?
                """,
                (profile_id, telemetry_id),
            ).fetchone()
            if row is None:
                raise KeyError("research telemetry is not available in this profile")
        actual_usage = None
        if row["usage_source"] is not None:
            actual_usage = {
                "cached_input_tokens": row["cached_input_tokens"],
                "input_tokens": row["input_tokens"],
                "model": row["model"],
                "output_tokens": row["output_tokens"],
                "reasoning_tokens": row["reasoning_tokens"],
                "recorded_at": row["usage_recorded_at"],
                "source": row["usage_source"],
            }
            actual_usage = {key: value for key, value in actual_usage.items() if value is not None}
        return {
            "actual_usage": actual_usage,
            "counter": row["counter"],
            "counter_version": row["counter_version"],
            "created_at": row["created_at"],
            "deduplicated_items": row["deduplicated_items"],
            "estimated_tokens": row["estimated_tokens"],
            "latency_ms": row["latency_ms"],
            "profile_id": row["profile_id"],
            "research_session_id": row["session_id"],
            "retrieval_calls": row["retrieval_calls"],
            "returned_items": row["returned_items"],
            "retries": row["retries"],
            "reused_evidence": bool(row["reused_evidence"]),
            "safety_margin": row["safety_margin"],
            "stage": row["stage"],
            "sufficient": bool(row["sufficient"]),
            "telemetry_id": row["telemetry_id"],
        }

    def list_research_telemetry(
        self,
        *,
        profile_id: str,
        session_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        _validate_knowledge_identifier(profile_id, "knowledge profile")
        _validate_knowledge_identifier(session_id, "research session")
        _validate_bounded_limit(limit, "research telemetry limit", maximum=500)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT telemetry_id FROM research_session_telemetry
                WHERE profile_id = ? AND session_id = ?
                ORDER BY telemetry_id DESC
                LIMIT ?
                """,
                (profile_id, session_id, limit),
            ).fetchall()
        return [self.research_telemetry_view(profile_id=profile_id, telemetry_id=row["telemetry_id"]) for row in rows]

    def cleanup_research_telemetry(self, *, profile_id: str, session_id: str, retain: int = 100) -> dict[str, int]:
        """Explicitly prune older minimal metrics; all source/archive records remain."""

        _validate_knowledge_identifier(profile_id, "knowledge profile")
        _validate_knowledge_identifier(session_id, "research session")
        _validate_bounded_limit(retain, "research telemetry retention", maximum=500)
        with self.connect() as conn:
            if conn.execute(
                "SELECT 1 FROM research_sessions WHERE profile_id = ? AND session_id = ?",
                (profile_id, session_id),
            ).fetchone() is None:
                raise KeyError("research session is not available in this profile")
            deleted = conn.execute(
                """
                DELETE FROM research_session_telemetry
                WHERE profile_id = ? AND session_id = ?
                  AND telemetry_id NOT IN (
                    SELECT telemetry_id FROM research_session_telemetry
                    WHERE profile_id = ? AND session_id = ?
                    ORDER BY telemetry_id DESC LIMIT ?
                  )
                """,
                (profile_id, session_id, profile_id, session_id, retain),
            ).rowcount
        return {"deleted": deleted, "retained_limit": retain}

    def delete_research_session(self, *, profile_id: str, session_id: str) -> dict[str, int]:
        """Explicit cleanup for a session and its checkpoints/telemetry only."""

        _validate_knowledge_identifier(profile_id, "knowledge profile")
        _validate_knowledge_identifier(session_id, "research session")
        with self.connect() as conn:
            deleted = conn.execute(
                "DELETE FROM research_sessions WHERE profile_id = ? AND session_id = ?",
                (profile_id, session_id),
            ).rowcount
        return {"sessions_deleted": deleted}

    @staticmethod
    def _ensure_knowledge_profile_conn(conn: sqlite3.Connection, profile_id: str) -> None:
        _validate_knowledge_identifier(profile_id, "knowledge profile")
        conn.execute(
            "INSERT OR IGNORE INTO knowledge_profiles(profile_id, created_at) VALUES (?, ?)",
            (profile_id, now_iso()),
        )

    @staticmethod
    def _assert_checkpoint_evidence_sets_conn(
        conn: sqlite3.Connection,
        profile_id: str,
        scope_id: str,
        checkpoint: ResearchCheckpoint,
    ) -> None:
        for evidence_set_id in checkpoint.evidence_set_ids:
            if conn.execute(
                "SELECT 1 FROM evidence_sets WHERE profile_id = ? AND scope_id = ? AND evidence_set_id = ?",
                (profile_id, scope_id, evidence_set_id),
            ).fetchone() is None:
                raise ValueError("research checkpoint evidence set is not available in this exact profile scope")

    @staticmethod
    def _insert_checkpoint_conn(
        conn: sqlite3.Connection,
        profile_id: str,
        session_id: str,
        checkpoint: ResearchCheckpoint,
    ) -> None:
        conn.execute(
            """
            INSERT INTO research_session_checkpoints(
                profile_id, session_id, checkpoint_at, summary, decisions_json,
                unresolved_questions_json, evidence_set_ids_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                session_id,
                checkpoint.created_at,
                checkpoint.summary,
                _canonical_json(list(checkpoint.decisions)),
                _canonical_json(list(checkpoint.unresolved_questions)),
                _canonical_json(list(checkpoint.evidence_set_ids)),
            ),
        )

    @staticmethod
    def _trim_research_telemetry_conn(conn: sqlite3.Connection, profile_id: str, session_id: str) -> None:
        conn.execute(
            """
            DELETE FROM research_session_telemetry
            WHERE profile_id = ? AND session_id = ?
              AND telemetry_id NOT IN (
                SELECT telemetry_id FROM research_session_telemetry
                WHERE profile_id = ? AND session_id = ?
                ORDER BY telemetry_id DESC LIMIT 500
              )
            """,
            (profile_id, session_id, profile_id, session_id),
        )

    def record_wiki_snapshot(
        self,
        *,
        snapshot_id: str,
        profile_id: str,
        scope_id: str,
        scope_hash: str,
        source_version: str,
        source_cursor: tuple[str, str, str, str],
        raw_path: str,
        raw_sha256: str,
        record_count: int,
        created_at: str,
    ) -> None:
        _validate_wiki_metadata(snapshot_id, profile_id, scope_id, scope_hash, source_version, raw_path, raw_sha256, record_count)
        _parse_aware_timestamp(created_at, "wiki snapshot created_at")
        _validate_wiki_cursor(source_cursor)
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO wiki_snapshots(snapshot_id, profile_id, scope_id, scope_hash, source_version, source_cursor_json, raw_path, raw_sha256, record_count, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (snapshot_id, profile_id, scope_id, scope_hash, source_version, json.dumps(source_cursor, separators=(",", ":")), raw_path, raw_sha256, record_count, created_at),
            )

    def record_wiki_page_revision(
        self,
        *,
        revision_id: str,
        snapshot_id: str,
        profile_id: str,
        scope_id: str,
        page_kind: str,
        subject_id: str,
        page_path: str,
        page_sha256: str,
        source_version: str,
        freshness_at: str,
        updated_at: str,
        assertions: list[dict[str, Any]],
    ) -> None:
        _validate_wiki_metadata(revision_id, profile_id, scope_id, page_sha256, source_version, page_path, page_sha256, 1)
        if page_kind not in {"people", "relationships", "projects", "decisions", "communication-styles"}:
            raise ValueError("wiki page kind is invalid")
        _validate_wiki_identifier(subject_id, "wiki subject")
        _parse_aware_timestamp(freshness_at, "wiki freshness_at")
        _parse_aware_timestamp(updated_at, "wiki updated_at")
        prepared_assertions = _validate_wiki_assertions(assertions)
        with self.connect() as conn:
            snapshot = conn.execute("SELECT profile_id, scope_id, source_version FROM wiki_snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
            if not snapshot or snapshot["profile_id"] != profile_id or snapshot["scope_id"] != scope_id:
                raise ValueError("wiki revision snapshot does not belong to the requested profile scope")
            conn.execute(
                """INSERT INTO wiki_page_revisions(revision_id, snapshot_id, profile_id, scope_id, page_kind, subject_id, page_path, page_sha256, source_version, freshness_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (revision_id, snapshot_id, profile_id, scope_id, page_kind, subject_id, page_path, page_sha256, source_version, freshness_at, updated_at),
            )
            for assertion_id, kind, confidence, citations in prepared_assertions:
                conn.execute("INSERT INTO wiki_assertions(assertion_id, revision_id, assertion_kind, confidence) VALUES (?, ?, ?, ?)", (assertion_id, revision_id, kind, confidence))
                for citation in citations:
                    chat_id, message_id = _parse_tg_citation(citation)
                    conn.execute("INSERT INTO wiki_assertion_citations(assertion_id, revision_id, citation, chat_id, message_id, source_version) VALUES (?, ?, ?, ?, ?, ?)", (assertion_id, revision_id, citation, chat_id, message_id, snapshot["source_version"]))

    def wiki_metadata(self, *, profile_id: str, scope_id: str) -> dict[str, list[dict[str, Any]]]:
        with self.connect() as conn:
            snapshots = [dict(row) for row in conn.execute("SELECT snapshot_id, scope_hash, source_version, source_cursor_json, raw_path, raw_sha256, record_count, created_at FROM wiki_snapshots WHERE profile_id = ? AND scope_id = ? ORDER BY created_at", (profile_id, scope_id))]
            revisions = [dict(row) for row in conn.execute("SELECT revision_id, snapshot_id, page_kind, subject_id, page_path, page_sha256, source_version, freshness_at, updated_at FROM wiki_page_revisions WHERE profile_id = ? AND scope_id = ? ORDER BY updated_at", (profile_id, scope_id))]
        return {"snapshots": snapshots, "revisions": revisions}

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

    def create_scope(
        self,
        name: str,
        chat_ids: list[int],
        since: str | None,
        until: str | None,
        media_policy: str = "none",
        transcription_policy: str = "off",
    ) -> None:
        if not chat_ids:
            raise ValueError("Scope must include at least one chat")
        _validate_media_policy(media_policy)
        _validate_transcription_policy(transcription_policy)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_scopes(name, since, until, media_policy, transcription_policy, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    since=excluded.since,
                    until=excluded.until,
                    media_policy=excluded.media_policy,
                    transcription_policy=excluded.transcription_policy
                """,
                (name, since, until, media_policy, transcription_policy, now_iso()),
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
            return {
                "name": row["name"],
                "since": row["since"],
                "until": row["until"],
                "media_policy": row["media_policy"],
                "transcription_policy": row["transcription_policy"],
                "chat_ids": chat_ids,
            }

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
                scopes.append(
                    {
                        "name": row["name"],
                        "since": row["since"],
                        "until": row["until"],
                        "media_policy": row["media_policy"],
                        "transcription_policy": row["transcription_policy"],
                        "chat_ids": chats,
                    }
                )
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

    def pending_media_ids_for_chats(self, chat_ids: list[int], media_policy: str = "all") -> set[int]:
        if not chat_ids:
            return set()
        where, params = _media_policy_where(chat_ids, media_policy)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT id FROM media WHERE {where} AND status != 'downloaded'",
                params,
            ).fetchall()
        return {int(row["id"]) for row in rows}

    def media_ids_for_chats(self, chat_ids: list[int], media_policy: str = "all") -> set[int]:
        if not chat_ids:
            return set()
        where, params = _media_policy_where(chat_ids, media_policy)
        with self.connect() as conn:
            rows = conn.execute(f"SELECT id FROM media WHERE {where}", params).fetchall()
        return {int(row["id"]) for row in rows}

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
                media_types=filters.media_types,
                has_link=filters.has_link,
            )
        filters = filters.normalized()
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
        filters = (filters or SearchFilters()).normalized()
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
                       COALESCE(c.title, '') AS chat_title, COALESCE(m.date, si.updated_at) AS date,
                       m.media_type AS media_type, m.sender_id AS sender_id, m.links_json AS links_json
                FROM semantic_index si
                LEFT JOIN chats c ON c.chat_id = si.chat_id
                LEFT JOIN messages m ON m.chat_id = si.chat_id AND m.message_id = si.message_id
                {where}
                """,
                params,
            ).fetchall()
        scored = []
        for row in rows:
            if filters.since is not None and row["date"] < filters.since:
                continue
            if filters.until is not None and row["date"] > filters.until:
                continue
            if filters.sender_id is not None and row["sender_id"] != filters.sender_id:
                continue
            if filters.media_type is not None and row["media_type"] != filters.media_type:
                continue
            if filters.media_types is not None and row["media_type"] not in filters.media_types:
                continue
            if filters.has_link is True and row["links_json"] == "[]":
                continue
            if filters.has_link is False and row["links_json"] != "[]":
                continue
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

    def message_context(
        self,
        chat_id: int,
        message_id: int,
        radius: int = 3,
        filters: SearchFilters | None = None,
    ) -> list[SearchResult]:
        where, params = _message_filter_sql("m", (filters or SearchFilters()).normalized())
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT m.chat_id, COALESCE(c.title, '') AS chat_title, m.message_id, m.date, m.text
                FROM messages m
                LEFT JOIN chats c ON c.chat_id = m.chat_id
                WHERE m.chat_id = ? AND m.message_id BETWEEN ? AND ?
                {where}
                ORDER BY m.message_id
                """,
                [chat_id, message_id - radius, message_id + radius, *params],
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


def _validate_wiki_metadata(
    identifier: str,
    profile_id: str,
    scope_id: str,
    digest: str,
    source_version: str,
    logical_path: str,
    file_digest: str,
    record_count: int,
) -> None:
    for value, field in ((identifier, "wiki identifier"), (profile_id, "wiki profile"), (scope_id, "wiki scope"), (source_version, "wiki source version")):
        _validate_wiki_identifier(value, field)
    for value, field in ((digest, "wiki digest"), (file_digest, "wiki file digest")):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError(f"{field} must be a SHA-256 hex digest")
    _validate_wiki_logical_path(logical_path)
    if record_count < 1:
        raise ValueError("wiki record count must be positive")


def _validate_wiki_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,127}", value):
        raise ValueError(f"{field} must be a normalized logical identifier")
    return value


def _validate_wiki_logical_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("wiki path must be a normalized POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or not path.parts or any(part in {"", ".", ".."} or ":" in part for part in path.parts):
        raise ValueError("wiki path must be a normalized POSIX relative path")
    for part in path.parts:
        _validate_wiki_identifier(part, "wiki path segment")
    return path


def _validate_wiki_cursor(value: object) -> None:
    if not isinstance(value, tuple) or len(value) != 4:
        raise ValueError("wiki source cursor is invalid")
    timestamp, citation, source_kind, source_id = value
    _parse_aware_timestamp(timestamp, "wiki source cursor timestamp")
    _parse_tg_citation(citation)
    if source_kind not in {"message", "transcript"}:
        raise ValueError("wiki source cursor kind is invalid")
    _validate_wiki_identifier(source_id, "wiki source cursor identifier")


def _validate_wiki_assertions(assertions: object) -> list[tuple[str, str, float, tuple[str, ...]]]:
    if not isinstance(assertions, list) or not assertions:
        raise ValueError("wiki revision requires assertions")
    prepared: list[tuple[str, str, float, tuple[str, ...]]] = []
    seen: set[str] = set()
    for assertion in assertions:
        if not isinstance(assertion, dict):
            raise ValueError("invalid wiki assertion metadata")
        assertion_id = _validate_wiki_identifier(assertion.get("assertion_id"), "wiki assertion")
        if assertion_id in seen:
            raise ValueError("wiki assertion IDs must be unique")
        seen.add(assertion_id)
        kind = assertion.get("kind")
        try:
            confidence = float(assertion.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid wiki assertion confidence") from exc
        if kind not in {"observed", "hypothesis"} or not 0 <= confidence <= 1:
            raise ValueError("invalid wiki assertion metadata")
        citations = assertion.get("citations")
        if not isinstance(citations, (list, tuple)) or not citations or len(set(citations)) != len(citations):
            raise ValueError("wiki assertion citations must be nonempty and unique")
        normalized = tuple(citations)
        for citation in normalized:
            _parse_tg_citation(citation)
        prepared.append((assertion_id, kind, confidence, normalized))
    return prepared


def _parse_tg_citation(value: object) -> tuple[int, int]:
    match = re.fullmatch(r"tg://chat/(-?\d+)/message/(\d+)", value) if isinstance(value, str) else None
    if not match:
        raise ValueError("wiki citation must be a tg:// message reference")
    return int(match.group(1)), int(match.group(2))


def _embedding_runtime_diagnostics() -> dict[str, str]:
    """Check optional runtime availability without importing it or loading a model."""

    return {"status": "available" if importlib.util.find_spec("sentence_transformers") else "not_installed"}


def _embedding_model_public(model: EmbeddingModelMetadata) -> dict[str, str | int]:
    return {
        "provider": model.provider,
        "model_id": model.model_id,
        "dimensions": model.dimensions,
        "model_source_hash": model.model_source_hash,
    }


def _validate_embedding_values(values: tuple[float, ...], dimensions: int) -> None:
    if len(values) != dimensions or any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
        raise ValueError("embedding vector has incompatible dimensions or non-finite values")


def _decode_embedding_values(payload: str, dimensions: int) -> tuple[float, ...]:
    try:
        decoded = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("stored embedding vector is malformed") from exc
    if not isinstance(decoded, list):
        raise ValueError("stored embedding vector is malformed")
    try:
        values = tuple(float(value) for value in decoded)
    except (TypeError, ValueError) as exc:
        raise ValueError("stored embedding vector is malformed") from exc
    _validate_embedding_values(values, dimensions)
    return values


def _fixed_embedding_batches(records: list[EmbeddingRecord], batch_size: int) -> list[EmbeddingBatch]:
    """Create deterministic rebuild work without consulting checkpoint state."""

    return [
        EmbeddingBatch(ordinal, tuple(records[start : start + batch_size]))
        for ordinal, start in enumerate(range(0, len(records), batch_size))
    ]


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_knowledge_identifier(value: object, label: str) -> str:
    try:
        return _validate_wiki_identifier(value, label)
    except ValueError as exc:
        raise ValueError(f"{label} must be a normalized logical identifier") from exc


def _validate_profile_scope(profile_id: str, scope_id: str, scope: KnowledgeScope) -> None:
    _validate_knowledge_identifier(profile_id, "knowledge profile")
    _validate_knowledge_identifier(scope_id, "knowledge scope")
    if not isinstance(scope, KnowledgeScope):
        raise ValueError("knowledge records must use an explicit knowledge scope")
    if scope.profile_id != profile_id:
        raise ValueError("knowledge records must use the exact profile scope")


def _validated_evidence_source_versions(
    evidence_set: EvidenceSetReference,
    source_versions: dict[str, str],
) -> dict[str, str]:
    if not isinstance(source_versions, dict):
        raise ValueError("evidence source_versions must be a citation-to-version mapping")
    citations = {citation for member in evidence_set.members for citation in member.citations}
    if set(source_versions) != citations:
        raise ValueError("evidence source_versions must exactly cover every cited source")
    validated: dict[str, str] = {}
    for citation, version in source_versions.items():
        _parse_tg_citation(citation)
        if not isinstance(version, str) or not version or len(version) > 256:
            raise ValueError("evidence source version must be a bounded non-empty string")
        try:
            validate_session_metadata({"source_version": version})
        except KnowledgeCatalogError as exc:
            raise ValueError("evidence source version contains forbidden private data") from exc
        validated[citation] = version
    return validated


def _validate_research_checkpoint(checkpoint: ResearchCheckpoint) -> None:
    if not isinstance(checkpoint, ResearchCheckpoint):
        raise ValueError("research checkpoint must use the compact checkpoint contract")
    # The domain object already validates bounded fields; this applies the
    # recursive key/path/secret policy to every persisted text value too.
    try:
        validate_session_metadata({
            "summary": checkpoint.summary,
            "decisions": list(checkpoint.decisions),
            "unresolved_questions": list(checkpoint.unresolved_questions),
            "evidence_set_ids": list(checkpoint.evidence_set_ids),
        })
    except KnowledgeCatalogError as exc:
        raise ValueError("research checkpoint contains forbidden private data") from exc
    _validate_compact_derived_text(checkpoint.summary, "research checkpoint summary")
    for value in (*checkpoint.decisions, *checkpoint.unresolved_questions):
        _validate_compact_derived_text(value, "research checkpoint note")


def _validate_research_session(session: ResearchSession) -> None:
    if not isinstance(session, ResearchSession):
        raise ValueError("research session must use the compact session contract")
    _validate_research_checkpoint(session.checkpoint)
    allowed_budgets = {
        "context_radius",
        "item_limit",
        "retry_budget",
        "safety_margin",
        "stage_budget",
        "token_budget",
        "tool_call_budget",
    }
    if any(key not in allowed_budgets for key, _ in session.budgets):
        raise ValueError("research session budget keys are not supported")
    try:
        validate_session_metadata({"purpose": session.purpose})
    except KnowledgeCatalogError as exc:
        raise ValueError("research session contains forbidden private data") from exc
    _validate_compact_derived_text(session.purpose, "research session purpose")


def _validate_safe_telemetry_label(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError(f"{label} must be a bounded non-empty string")
    try:
        validate_session_metadata({label.replace(" ", "_"): value})
    except KnowledgeCatalogError as exc:
        raise ValueError(f"{label} contains forbidden private data") from exc
    return value


def _validate_compact_derived_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    # Compact derived notes are allowed, but they must never become an escape
    # hatch for full prompts, transcripts, or hidden reasoning.
    if re.search(r"(?i)\b(?:hidden reasoning|chain[ -]?of[ -]?thought|full (?:codex )?transcript|full prompt)\b", value):
        raise ValueError(f"{label} must not contain hidden reasoning, full prompts, or full transcripts")
    return value


def _validated_telemetry_stage(value: RetrievalStage | str) -> str:
    try:
        return RetrievalStage(value).value
    except (TypeError, ValueError) as exc:
        raise ValueError("research telemetry stage is invalid") from exc


def _validate_bounded_limit(value: object, label: str, *, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise ValueError(f"{label} must be an integer in 1..{maximum}")
    return value


def tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r"[\w']+", text.lower()) if len(token) > 2}


def _validate_media_policy(value: str) -> None:
    allowed = {"none", "voice", "audio", "photo", "video", "document", "all"}
    selected = {item.strip() for item in value.split(",") if item.strip()}
    if not selected or not selected <= allowed:
        raise ValueError(f"Invalid media policy: {value}")
    if "all" in selected and len(selected) > 1:
        raise ValueError("media policy 'all' cannot be combined with other values")
    if "none" in selected and len(selected) > 1:
        raise ValueError("media policy 'none' cannot be combined with other values")


def _validate_transcription_policy(value: str) -> None:
    if value not in {"off", "telegram", "local", "auto"}:
        raise ValueError(f"Invalid transcription policy: {value}")


def _media_policy_where(chat_ids: list[int], media_policy: str) -> tuple[str, list[Any]]:
    selected = {item.strip() for item in media_policy.split(",") if item.strip()}
    if "none" in selected:
        return "1 = 0", []
    chat_placeholders = ", ".join("?" for _ in chat_ids)
    params: list[Any] = list(chat_ids)
    where = f"chat_id IN ({chat_placeholders})"
    if "all" not in selected:
        type_placeholders = ", ".join("?" for _ in selected)
        where += f" AND media_type IN ({type_placeholders})"
        params.extend(sorted(selected))
    return where, params


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
