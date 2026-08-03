from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tg_recall.cli import main
from tg_recall.config import AIAccessPolicy, AppConfig, save_config
from tg_recall.models import ChatRecord, MessageRecord
from tg_recall.storage import Database
from tg_recall.wiki_memory import (
    AssertionKind,
    AuthorizedWikiScope,
    PageKind,
    RawSourceRecord,
    WikiAssertion,
    WikiMemoryStore,
    WikiPageDraft,
)


def archive(tmp_path: Path) -> tuple[Path, AppConfig, Database]:
    home = tmp_path / "home"
    cfg = AppConfig.default(home, "work")
    save_config(cfg, home=home)
    db = Database(cfg.db_path)
    db.migrate()
    db.upsert_chat(ChatRecord(chat_id=10, title="Synthetic", chat_type="group"))
    db.upsert_message(
        MessageRecord(
            chat_id=10,
            message_id=7,
            date=datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
            text="Synthetic bounded decision",
            sender_name="Test",
        )
    )
    db.create_scope("work-scope", [10], "2026-01-01", None, "none", "off")
    return home, cfg, db


def create_args(home: Path, name: str = "decision-pack") -> list[str]:
    return [
        "--home", str(home), "--profile", "work", "--json", "pack", "create", name,
        "--chat", "10", "--since", "2026-01-01", "--max-records", "5", "--token-budget", "1000",
    ]


def test_pack_create_inspect_verify_are_additive_and_offline(tmp_path: Path, capsys) -> None:
    home, cfg, _ = archive(tmp_path)

    assert main(create_args(home)) == 0
    created = json.loads(capsys.readouterr().out)
    pack_path = Path(created["path"])
    assert created == {
        "operation": "pack_create",
        "path": str(pack_path),
        "kind": "raw",
        "files": 1,
        "reused_paths": [],
        "schema_version": 1,
    }

    # Pack inspection and verification only read the pack.  The source archive
    # need not be present on the receiving machine.
    Path(cfg.db_path).unlink()
    assert main(["--home", str(home), "--profile", "work", "--json", "pack", "inspect", str(pack_path)]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["operation"] == "pack_inspect"
    assert inspected["kind"] == "raw"
    assert inspected["scope"]["chat_ids"] == [10]

    assert main(["--home", str(home), "--profile", "work", "--json", "pack", "verify", str(pack_path)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_pack_requires_explicit_scope_and_detects_tampering(tmp_path: Path, capsys) -> None:
    home, _, _ = archive(tmp_path)
    assert main([
        "--home", str(home), "--profile", "work", "--json", "pack", "create", "bad-pack",
        "--max-records", "5", "--token-budget", "1000",
    ]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "PackError"

    assert main(create_args(home)) == 0
    created = json.loads(capsys.readouterr().out)
    pack_path = Path(created["path"])
    (pack_path / "raw" / "evidence.jsonl").write_text("tampered", encoding="utf-8")
    assert main(["--home", str(home), "--profile", "work", "--json", "pack", "verify", str(pack_path)]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "PackVerificationError"


def test_agent_pack_paths_stay_private_and_external_create_is_denied(tmp_path: Path, monkeypatch, capsys) -> None:
    home, cfg, _ = archive(tmp_path)
    cfg.ai_access = AIAccessPolicy(enabled=True, allowed_chat_ids=[10], max_results=5)
    save_config(cfg, home=home)
    monkeypatch.setenv("TG_RECALL_AI_MODE", "1")

    outside = tmp_path / "outside" / "decision-pack"
    assert main(create_args(home) + ["--output", str(outside)]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "external_pack_path_forbidden"
    assert not outside.exists()

    assert main(create_args(home)) == 0
    created = json.loads(capsys.readouterr().out)
    assert main(["--home", str(home), "--profile", "work", "--json", "pack", "inspect", str(outside)]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "private_pack_path_required"
    assert main(["--home", str(home), "--profile", "work", "--json", "pack", "verify", created["path"]]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_agent_pack_keeps_requested_scope_narrower_than_policy(tmp_path: Path, monkeypatch, capsys) -> None:
    home, cfg, db = archive(tmp_path)
    db.upsert_chat(ChatRecord(chat_id=11, title="Other synthetic", chat_type="group"))
    db.upsert_message(
        MessageRecord(chat_id=11, message_id=8, date=datetime(2026, 1, 3, tzinfo=UTC), text="Must not be exported")
    )
    cfg.ai_access = AIAccessPolicy(
        enabled=True,
        allowed_chat_ids=[10, 11],
        max_results=50,
        allowed_since="2026-01-01",
        allowed_until="2026-01-31",
    )
    save_config(cfg, home=home)
    monkeypatch.setenv("TG_RECALL_AI_MODE", "1")

    assert main([
        "--home", str(home), "--profile", "work", "--json", "pack", "create", "narrow-pack",
        "--chat", "10", "--since", "2026-01-02", "--until", "2026-01-03",
        "--max-records", "2", "--token-budget", "1000",
    ]) == 0
    created = json.loads(capsys.readouterr().out)
    manifest = json.loads((Path(created["path"]) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["scope"] == {
        "chat_ids": [10],
        "max_records": 2,
        "saved_scope": None,
        "since": "2026-01-02T00:00:00Z",
        "token_budget": 1000,
        "until": "2026-01-03T00:00:00Z",
    }
    raw = (Path(created["path"]) / "raw" / "evidence.jsonl").read_text(encoding="utf-8")
    assert "Must not be exported" not in raw

    cfg.ai_access.allowed_chat_ids = [10]
    save_config(cfg, home=home)
    assert main([
        "--home", str(home), "--profile", "work", "--json", "pack", "create", "denied-pack",
        "--chat", "11", "--since", "2026-01-02", "--max-records", "2", "--token-budget", "1000",
    ]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "chat_not_allowed"
    assert not (Path(cfg.exports_dir) / "denied-pack").exists()


def test_pack_can_embed_only_explicit_verified_wiki_revisions(tmp_path: Path, capsys) -> None:
    home, cfg, _ = archive(tmp_path)
    scope = AuthorizedWikiScope("work", "work-scope", (10,))
    store = WikiMemoryStore(cfg.wiki_dir)
    compiled = store.compile(
        scope,
        [
            RawSourceRecord(
                citation="tg://chat/10/message/7",
                chat_id=10,
                timestamp="2026-01-02T03:04:00Z",
                text="Synthetic bounded decision",
                source_id="message-7",
            )
        ],
        [
            WikiPageDraft(
                kind=PageKind.DECISION,
                subject_id="synthetic-decision",
                title="Synthetic decision",
                assertions=(
                    WikiAssertion(
                        text="A synthetic decision was recorded.",
                        kind=AssertionKind.OBSERVED,
                        confidence=1,
                        citations=("tg://chat/10/message/7",),
                    ),
                ),
            )
        ],
        created_at="2026-01-03T00:00:00Z",
    )
    revision_id = compiled.page_revisions[0].revision_id

    assert main([
        "--home", str(home), "--profile", "work", "--json", "pack", "create", "mixed-pack",
        "--scope", "work-scope", "--max-records", "5", "--token-budget", "5000",
        "--wiki-revision", revision_id,
    ]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["kind"] == "mixed"
    manifest = json.loads((Path(created["path"]) / "manifest.json").read_text(encoding="utf-8"))
    assert {file["logical_path"] for file in manifest["files"]} == {
        "raw/evidence.jsonl",
        f"wiki/{revision_id}.assertions.json",
        f"wiki/{revision_id}.md",
    }


def test_legacy_export_command_remains_compatible(tmp_path: Path, capsys) -> None:
    home, _, _ = archive(tmp_path)
    assert main(["--home", str(home), "--profile", "work", "--json", "export", "--chat", "10"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["messages"] == 1
    assert Path(payload["path"]).is_file()
