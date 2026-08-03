from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import tg_recall.export_packs as export_packs

from tg_recall.export_packs import (
    ExportScope,
    PackError,
    PackVerificationError,
    WikiPage,
    build_pack,
    inspect_pack,
    verify_pack,
)


def scope() -> ExportScope:
    return ExportScope(chat_ids=(100,), since="2026-01-01T00:00:00Z", max_records=4, token_budget=8_000)


def evidence(text: str = "A private decision") -> dict[str, object]:
    return {"chat_id": 100, "message_id": 7, "timestamp": "2026-01-02T03:04:05Z", "text": text, "sender_name": "Ada"}


def wiki() -> WikiPage:
    return WikiPage(
        slug="decision-7",
        markdown="# Decision\nUse the private archive.",
        assertions=({"claim": "A decision exists", "source_citations": ["tg://chat/100/message/7"]},),
        snapshot_id="snapshot-01",
        updated_at="2026-01-03T00:00:00Z",
    )


def build(tmp_path: Path, **kwargs: object):
    arguments: dict[str, object] = {
        "name": "decision-pack",
        "scope": scope(),
        "raw_evidence": [evidence()],
        "wiki_pages": [wiki()],
        "profile_alias": "work",
        "default_exports_dir": tmp_path / "profile" / "exports",
        "profile_cache_dir": tmp_path / "profile" / "cache",
        "source_snapshot_ids": ["snapshot-01"],
        "created_at": "2026-01-04T00:00:00Z",
    }
    arguments.update(kwargs)
    return build_pack(**arguments)  # type: ignore[arg-type]


def test_builds_deterministic_mixed_pack_and_verifies_offline(tmp_path: Path) -> None:
    result = build(tmp_path)

    assert result.path == tmp_path / "profile" / "exports" / "decision-pack"
    assert verify_pack(result.path) == {"files": 3, "kind": "mixed", "ok": True, "schema_version": 1, "strict": True}
    manifest = inspect_pack(result.path)
    assert manifest["external_destination"] is False
    assert str(tmp_path) not in json.dumps(manifest)
    assert [item["logical_path"] for item in manifest["files"]] == [
        "raw/evidence.jsonl", "wiki/decision-7.assertions.json", "wiki/decision-7.md"
    ]


def test_requires_bounded_scope_and_refuses_out_of_scope_or_over_budget(tmp_path: Path) -> None:
    with pytest.raises(PackError, match="scope requires"):
        build_pack(name="bad", scope=ExportScope(max_records=1, token_budget=1), profile_alias="work", default_exports_dir=tmp_path)
    with pytest.raises(PackError, match="outside declared chat"):
        build(tmp_path, raw_evidence=[{**evidence(), "chat_id": 101}])
    with pytest.raises(PackError, match="token_budget"):
        build(tmp_path, scope=ExportScope(chat_ids=(100,), since="2026-01-01T00:00:00Z", max_records=4, token_budget=1))


def test_saved_scope_is_provenance_not_an_unresolved_export_boundary(tmp_path: Path) -> None:
    with pytest.raises(PackError, match="resolved chat_ids"):
        build_pack(
            name="bad",
            scope=ExportScope(saved_scope="work", max_records=1, token_budget=1_000),
            profile_alias="work",
            default_exports_dir=tmp_path,
        )


def test_wiki_citations_must_stay_inside_resolved_chat_scope(tmp_path: Path) -> None:
    foreign_wiki = WikiPage(
        slug="foreign",
        markdown="# Foreign",
        assertions=({"claim": "outside", "source_citations": ["tg://chat/999/message/7"]},),
        snapshot_id="snapshot-02",
        updated_at="2026-01-03T00:00:00Z",
    )
    with pytest.raises(PackError, match="exceed the declared chat scope"):
        build(tmp_path, wiki_pages=[foreign_wiki])


def test_full_serialized_pack_budget_is_checked_before_staging(tmp_path: Path) -> None:
    with pytest.raises(PackError, match="token_budget"):
        build(
            tmp_path,
            scope=ExportScope(chat_ids=(100,), since="2026-01-01T00:00:00Z", max_records=4, token_budget=1),
        )
    assert not (tmp_path / "profile" / "exports" / "decision-pack").exists()
    assert not (tmp_path / "profile" / "cache").exists()


def test_raw_export_whitelists_evidence_fields_and_excludes_private_paths(tmp_path: Path) -> None:
    result = build(tmp_path, raw_evidence=[{**evidence(), "session_path": "C:/secret.session", "credentials": {"api_hash": "secret"}}])
    raw = json.loads((result.path / "raw" / "evidence.jsonl").read_text(encoding="utf-8"))
    assert "session_path" not in raw
    assert "credentials" not in raw
    assert "C:/secret.session" not in (result.path / "manifest.json").read_text(encoding="utf-8")


def test_tampering_undeclared_content_and_traversal_are_rejected(tmp_path: Path) -> None:
    result = build(tmp_path)
    raw = result.path / "raw" / "evidence.jsonl"
    raw.write_text("tampered", encoding="utf-8")
    with pytest.raises(PackVerificationError, match="size mismatch|hash mismatch"):
        verify_pack(result.path)

    result = build(tmp_path / "second")
    (result.path / "surprise.txt").write_text("not declared", encoding="utf-8")
    with pytest.raises(PackVerificationError, match="declared files mismatch"):
        verify_pack(result.path)
    manifest_path = result.path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["logical_path"] = "../escape.jsonl"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackVerificationError, match="unsafe logical path"):
        verify_pack(result.path)


def test_links_and_stale_wiki_are_rejected(tmp_path: Path) -> None:
    result = build(tmp_path)
    with pytest.raises(PackVerificationError, match="stale wiki content"):
        verify_pack(result.path, max_wiki_age_seconds=0)

    link = result.path / "wiki" / "escape.md"
    try:
        os.symlink(tmp_path / "outside", link)
    except OSError:
        pytest.skip("symbolic links unavailable in this environment")
    with pytest.raises(PackVerificationError, match="symbolic link"):
        verify_pack(result.path)


def test_incremental_reuses_only_a_verified_immutable_pack(tmp_path: Path) -> None:
    first = build(tmp_path / "one")
    second = build(tmp_path / "two", previous_pack=first.path)
    assert set(second.reused_paths) == {"raw/evidence.jsonl", "wiki/decision-7.assertions.json", "wiki/decision-7.md"}
    assert (first.path / "raw" / "evidence.jsonl").read_bytes() == (second.path / "raw" / "evidence.jsonl").read_bytes()
    (first.path / "raw" / "evidence.jsonl").write_text("bad", encoding="utf-8")
    with pytest.raises(PackVerificationError):
        build(tmp_path / "three", previous_pack=first.path)


def test_external_destination_is_explicit_and_not_embedded(tmp_path: Path) -> None:
    output = tmp_path / "outside" / "decision-pack"
    result = build(tmp_path, output_path=output)
    assert result.path == output.resolve()
    assert inspect_pack(result.path)["external_destination"] is True
    assert str(output.parent.resolve()) not in (result.path / "manifest.json").read_text(encoding="utf-8")


def test_external_parent_is_never_hardened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hardened: list[Path] = []
    original = export_packs._harden_directory

    def record_hardening(path: Path) -> None:
        hardened.append(path.resolve())
        original(path)

    monkeypatch.setattr(export_packs, "_harden_directory", record_hardening)
    external_parent = tmp_path / "arbitrary-external-parent"
    result = build(tmp_path, output_path=external_parent / "decision-pack")

    assert result.path.exists()
    assert external_parent.resolve() not in hardened


def test_interrupted_generation_leaves_no_completed_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(_source: Path, _destination: Path) -> None:
        raise OSError("simulated interruption")

    monkeypatch.setattr(export_packs.os, "replace", interrupted)
    with pytest.raises(PackError, match="atomically finalize"):
        build(tmp_path)
    assert not (tmp_path / "profile" / "exports" / "decision-pack").exists()
    assert not list((tmp_path / "profile" / "cache").glob(".decision-pack.*"))
