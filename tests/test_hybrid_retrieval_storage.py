from __future__ import annotations

from datetime import UTC, datetime

from tg_recall.hybrid_retrieval import EmbeddingModelMetadata
from tg_recall.models import ChatRecord, MessageRecord, SearchFilters
from tg_recall.storage import Database, SCHEMA_VERSION


class FixtureProvider:
    metadata = EmbeddingModelMetadata("fixture", "local-fixture", 3, "a" * 64)

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        self.calls.append(texts)
        return [(1.0, 0.0, 0.0) if "alpha" in text else (0.0, 1.0, 0.0) for text in texts]


def seed(db: Database) -> None:
    db.migrate()
    db.upsert_chat(ChatRecord(chat_id=10, title="Allowed", chat_type="group"))
    db.upsert_chat(ChatRecord(chat_id=20, title="Other", chat_type="group"))
    db.upsert_message(MessageRecord(chat_id=10, message_id=1, date=datetime(2026, 1, 1, tzinfo=UTC), text="alpha local evidence"))
    db.upsert_message(MessageRecord(chat_id=20, message_id=1, date=datetime(2026, 1, 2, tzinfo=UTC), text="beta other evidence"))


def test_v5_migration_adds_private_vector_lifecycle_tables(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    with db.connect() as conn:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert {"embedding_models", "embedding_vectors", "embedding_index_checkpoints"} <= tables
    assert version == SCHEMA_VERSION


def test_bounded_build_resumes_only_missing_batches_and_tracks_staleness(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    seed(db)
    provider = FixtureProvider()

    first = db.build_embedding_index(provider, batch_size=1, max_batches=1)
    assert first["batches_committed"] == 1
    assert first["vectors_written"] == 1
    assert first["remaining_records"] == 1
    assert provider.calls == [["alpha local evidence"]]

    second = db.build_embedding_index(provider, batch_size=1)
    assert second["batches_committed"] == 1
    assert provider.calls == [["alpha local evidence"], ["beta other evidence"]]
    assert second["index"]["status"] == "current"

    db.upsert_message(MessageRecord(chat_id=10, message_id=1, date=datetime(2026, 1, 1, tzinfo=UTC), text="alpha changed evidence"))
    stale = db.embedding_index_status(provider.metadata)
    assert stale["status"] == "stale"
    assert stale["stale"] == 1
    assert stale["current"] == 1


def test_vector_candidates_enforce_filters_and_remove_keeps_archive_sources(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    seed(db)
    provider = FixtureProvider()
    db.build_embedding_index(provider, batch_size=2)

    candidates = db.vector_candidates((1.0, 0.0, 0.0), provider.metadata, filters=SearchFilters(chat_id=10))
    assert [(item.chat_id, item.message_id, item.channel.value) for item in candidates] == [(10, 1, "semantic")]

    removed = db.remove_embedding_index(model_identity=provider.metadata.identity)
    assert removed == {"scope": "model", "models_deleted": 1}
    assert db.embedding_index_status(provider.metadata)["missing"] == 2
    assert [record.text for record in db.embedding_records()] == ["alpha local evidence", "beta other evidence"]


def test_changed_model_shape_is_reported_as_incompatible_and_not_mixed(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    seed(db)
    provider = FixtureProvider()
    db.build_embedding_index(provider, batch_size=2)

    changed_shape = EmbeddingModelMetadata("fixture", "local-fixture", 4, "a" * 64)
    status = db.embedding_index_status(changed_shape)
    assert status["status"] == "incompatible"
    assert status["vectors"] == 0
    assert status["incompatible_models"] == 1


def test_embedding_diagnostics_are_sanitized_and_do_not_load_a_model(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    seed(db)
    diagnostics = db.diagnostics()
    assert diagnostics["embedding_runtime"]["status"] in {"available", "not_installed"}
    assert diagnostics["embedding_index"] == {
        "status": "not_configured",
        "stored_models": 0,
        "vectors": 0,
        "checkpoints": 0,
        "models": [],
    }
