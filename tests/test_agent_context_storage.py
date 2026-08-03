from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path

import pytest

from tg_recall.context_budgeting import ActualUsage, RetrievalStage
from tg_recall.knowledge_catalog import EvidenceMemberKind, EvidenceSetMember, EvidenceSetReference, KnowledgeScope, ResearchCheckpoint, ResearchSession
from tg_recall.backup import create_backup, restore_backup
from tg_recall.config import AppConfig, save_config
from tg_recall.paths import AppPaths
from tg_recall.storage import Database, SCHEMA_VERSION


def scope(profile_id: str = "work") -> KnowledgeScope:
    return KnowledgeScope(profile_id, (100, 200))


def evidence(profile_id: str = "work") -> EvidenceSetReference:
    return EvidenceSetReference(
        evidence_set_id="payment-research",
        scope=scope(profile_id),
        purpose="Verify the payment deadline",
        query="payment deadline",
        members=(
            EvidenceSetMember(
                "raw-payment",
                EvidenceMemberKind.RAW,
                "message-42",
                ("tg://chat/100/message/42",),
                "message-v1",
            ),
        ),
        summary="Cited navigation note; verify the original message before reporting.",
        created_at="2026-08-03T10:00:00Z",
        revision="set-v1",
        topics=("payment", "deadline"),
    )


def checkpoint(at: str = "2026-08-03T10:00:00Z") -> ResearchCheckpoint:
    return ResearchCheckpoint(
        summary="One cited decision was found.",
        decisions=("Use the cited source for the answer.",),
        unresolved_questions=("Check whether it was later changed.",),
        evidence_set_ids=("payment-research",),
        created_at=at,
    )


def session(profile_id: str = "work", *, at: str = "2026-08-03T10:00:00Z") -> ResearchSession:
    return ResearchSession(
        "payment-session",
        scope(profile_id),
        "Verify the payment deadline",
        (("token_budget", 4_000), ("tool_call_budget", 2)),
        checkpoint(at),
        at,
        at,
    )


def seed(db: Database, profile_id: str = "work") -> None:
    db.migrate()
    db.record_evidence_set(
        profile_id=profile_id,
        scope_id="work-scope",
        evidence_set=evidence(profile_id),
        source_versions={"tg://chat/100/message/42": "message-v1"},
    )


def test_v6_schema_persists_immutable_references_and_versions_without_raw_archive_columns(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    seed(db)
    view = db.evidence_set_view(profile_id="work", evidence_set_id="payment-research")
    with db.connect() as conn:
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        evidence_columns = {row["name"] for row in conn.execute("PRAGMA table_info(evidence_sets)")}
        source = conn.execute("SELECT citation, source_version FROM evidence_member_sources").fetchone()

    assert version == SCHEMA_VERSION
    assert view["members"] == [{
        "kind": "raw",
        "logical_id": "message-42",
        "member_id": "raw-payment",
        "sources": [{"citation": "tg://chat/100/message/42", "source_version": "message-v1"}],
        "version": "message-v1",
    }]
    assert tuple(source) == ("tg://chat/100/message/42", "message-v1")
    assert not {"text", "prompt", "transcript", "reasoning", "credential", "session_path"} & evidence_columns
    with pytest.raises(ValueError, match="immutable"):
        db.record_evidence_set(
            profile_id="work",
            scope_id="work-scope",
            evidence_set=evidence(),
            source_versions={"tg://chat/100/message/42": "message-v1"},
        )


def test_profile_and_exact_scope_isolation_rejects_cross_profile_access(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    seed(db, "work")
    with pytest.raises(KeyError, match="this profile"):
        db.evidence_set_view(profile_id="personal", evidence_set_id="payment-research")
    with pytest.raises(ValueError, match="exact profile scope"):
        db.record_evidence_set(
            profile_id="personal",
            scope_id="work-scope",
            evidence_set=evidence("work"),
            source_versions={"tg://chat/100/message/42": "message-v1"},
        )
    with pytest.raises(ValueError, match="exact profile scope"):
        db.create_research_session(profile_id="work", scope_id="another-scope", session=session())

    db.record_evidence_set(
        profile_id="personal",
        scope_id="personal-scope",
        evidence_set=evidence("personal"),
        source_versions={"tg://chat/100/message/42": "personal-v1"},
    )
    assert [item["profile_id"] for item in db.list_evidence_sets(profile_id="work")] == ["work"]
    assert [item["profile_id"] for item in db.list_evidence_sets(profile_id="personal")] == ["personal"]


def test_research_checkpoints_are_monotonic_and_telemetry_keeps_actual_usage_separate(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    seed(db)
    created = db.create_research_session(profile_id="work", scope_id="work-scope", session=session())
    assert created["checkpoint_count"] == 1

    updated = db.append_research_checkpoint(
        profile_id="work",
        session_id="payment-session",
        checkpoint=checkpoint("2026-08-03T11:00:00Z"),
    )
    assert updated["checkpoint"]["created_at"] == "2026-08-03T11:00:00Z"
    assert updated["checkpoint_count"] == 2
    with pytest.raises(ValueError, match="monotonic"):
        db.append_research_checkpoint(profile_id="work", session_id="payment-session", checkpoint=checkpoint())

    telemetry = db.record_research_telemetry(
        profile_id="work",
        session_id="payment-session",
        stage=RetrievalStage.CATALOG,
        retrieval_calls=1,
        returned_items=3,
        deduplicated_items=2,
        retries=0,
        latency_ms=17,
        estimated_tokens=432,
        counter="utf8-json-conservative",
        counter_version="1",
        safety_margin=0.15,
        reused_evidence=True,
        sufficient=True,
        actual_usage=ActualUsage("codex-host", "gpt-5.6-luna", input_tokens=500, cached_input_tokens=20, output_tokens=30),
    )
    assert telemetry["estimated_tokens"] == 432
    assert telemetry["actual_usage"] == {
        "cached_input_tokens": 20,
        "input_tokens": 500,
        "model": "gpt-5.6-luna",
        "output_tokens": 30,
        "recorded_at": telemetry["created_at"],
        "source": "codex-host",
    }
    assert db.list_research_telemetry(profile_id="work", session_id="payment-session") == [telemetry]


@pytest.mark.parametrize(
    ("operation", "match"),
    (
        ("unsafe_source_version", "forbidden private"),
        ("unsafe_budget_key", "budget keys"),
        ("unsafe_checkpoint_content", "hidden reasoning, full prompts, or full transcripts"),
        ("unsafe_actual_model", "forbidden private"),
    ),
)
def test_storage_rejects_secrets_paths_and_recursive_unsafe_session_metadata(tmp_path, operation: str, match: str) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    if operation == "unsafe_source_version":
        with pytest.raises(ValueError, match=match):
            db.record_evidence_set(
                profile_id="work",
                scope_id="work-scope",
                evidence_set=evidence(),
                source_versions={"tg://chat/100/message/42": "api_key=secret"},
            )
        return
    seed(db)
    if operation == "unsafe_budget_key":
        unsafe = ResearchSession(
            "unsafe-session", scope(), "Safe purpose", (("api_key", 1),), checkpoint(),
            "2026-08-03T10:00:00Z", "2026-08-03T10:00:00Z",
        )
        with pytest.raises(ValueError, match=match):
            db.create_research_session(profile_id="work", scope_id="work-scope", session=unsafe)
        return
    if operation == "unsafe_checkpoint_content":
        unsafe = ResearchSession(
            "unsafe-session",
            scope(),
            "Safe purpose",
            (("token_budget", 1),),
            ResearchCheckpoint("Full Codex transcript: private content", evidence_set_ids=("payment-research",), created_at="2026-08-03T10:00:00Z"),
            "2026-08-03T10:00:00Z",
            "2026-08-03T10:00:00Z",
        )
        with pytest.raises(ValueError, match=match):
            db.create_research_session(profile_id="work", scope_id="work-scope", session=unsafe)
        return
    db.create_research_session(profile_id="work", scope_id="work-scope", session=session())
    with pytest.raises(ValueError, match=match):
        db.record_research_telemetry(
            profile_id="work", session_id="payment-session", stage="raw",
            retrieval_calls=1, returned_items=1, deduplicated_items=1, retries=0,
            latency_ms=1, estimated_tokens=1, counter="safe", counter_version="1",
            safety_margin=0.15, reused_evidence=False, sufficient=False,
            actual_usage=ActualUsage("host", "C:/private/model", input_tokens=1),
        )


def test_telemetry_retention_and_explicit_session_cleanup_are_bounded(tmp_path) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    seed(db)
    db.create_research_session(profile_id="work", scope_id="work-scope", session=session())
    for _ in range(3):
        db.record_research_telemetry(
            profile_id="work", session_id="payment-session", stage="raw",
            retrieval_calls=1, returned_items=1, deduplicated_items=1, retries=0,
            latency_ms=1, estimated_tokens=1, counter="safe", counter_version="1",
            safety_margin=0.15, reused_evidence=False, sufficient=False,
        )
    assert db.cleanup_research_telemetry(profile_id="work", session_id="payment-session", retain=1) == {
        "deleted": 2,
        "retained_limit": 1,
    }
    assert len(db.list_research_telemetry(profile_id="work", session_id="payment-session")) == 1
    assert db.delete_research_session(profile_id="work", session_id="payment-session") == {"sessions_deleted": 1}
    with pytest.raises(KeyError, match="this profile"):
        db.research_session_view(profile_id="work", session_id="payment-session")


def test_context_state_is_profile_private_in_sqlite_backup_and_not_a_release_asset(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = AppConfig.default(home, "work")
    config.ensure_dirs()
    save_config(config, home=home)
    db = Database(config.db_path)
    seed(db)
    db.create_research_session(profile_id="work", scope_id="work-scope", session=session())
    backup_path = tmp_path / "context-state.zip"

    create_backup(config, backup_path)
    with zipfile.ZipFile(backup_path) as archive:
        assert "profile/archive.sqlite3" in archive.namelist()
        assert not any("telegram.session" in name or "credentials" in name for name in archive.namelist())
    restored = restore_backup(backup_path, home=home, profile="restored")
    restored_db = sqlite3.connect(AppPaths.resolve(home, "restored").db_path)
    try:
        assert restored_db.execute("SELECT COUNT(*) FROM evidence_sets WHERE profile_id = 'work'").fetchone()[0] == 1
        assert restored_db.execute("SELECT COUNT(*) FROM research_sessions WHERE profile_id = 'work'").fetchone()[0] == 1
    finally:
        restored_db.close()
    assert restored["profile"] == "restored"
