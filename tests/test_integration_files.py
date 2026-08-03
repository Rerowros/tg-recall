from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from tg_recall import integration_files
from tg_recall.integration_files import (
    IntegrationComponent,
    IntegrationConflictError,
    IntegrationPathError,
    IntegrationScope,
    IntegrationStatus,
    PlannedFileChange,
    apply_file_change,
    apply_marked_block,
    apply_owned_toml_block,
    default_capabilities,
    ensure_integration_directory,
    load_ownership_state,
    merge_json_mcp,
    ownership_record,
    plan_dedicated_file,
    render_dedicated_file,
    render_marked_block,
    save_ownership_state,
    validate_integration_target,
)
from tg_recall.security import SecurityFinding


def test_capability_inventory_and_result_json_are_deterministic() -> None:
    capabilities = default_capabilities()

    assert {item.harness for item in capabilities} == {"codex", "claude-code", "cursor", "generic"}
    assert all(
        item.as_json()["lifecycle"] == ["preview", "install", "status", "refresh", "uninstall"] for item in capabilities
    )


def test_marked_block_is_idempotent_and_preserves_unrelated_content() -> None:
    original = "# Existing project instructions\nKeep this line.\n"
    first = apply_marked_block(original, "Use cited local retrieval.", guide_version="0.5.0")
    second = apply_marked_block(first.content, "Use cited local retrieval.", guide_version="0.5.0")

    assert first.status is IntegrationStatus.INSTALLED
    assert first.content is not None and first.content.startswith(original)
    assert second.status is IntegrationStatus.UNCHANGED
    assert second.content == first.content


@pytest.mark.parametrize(
    "content",
    [
        "<!-- tg-recall:begin owner=tg-recall schema=1 guide=0.5.0 sha256=" + "0" * 64 + " -->\n",
        render_marked_block("one", guide_version="0.5.0") + render_marked_block("two", guide_version="0.5.0"),
    ],
)
def test_marked_block_rejects_malformed_or_duplicate_ownership(content: str) -> None:
    planned = apply_marked_block(content, "replacement", guide_version="0.5.0")

    assert planned.status is IntegrationStatus.CONFLICT
    assert planned.content is None


def test_marked_block_rejects_user_edit_inside_owned_body() -> None:
    existing = render_marked_block("Original body", guide_version="0.5.0").replace("Original", "Edited")

    planned = apply_marked_block(existing, "Replacement", guide_version="0.5.1", action="refresh")

    assert planned.status is IntegrationStatus.CONFLICT
    assert planned.conflict == "owned instruction block was edited"


def test_dedicated_file_rejects_unowned_content() -> None:
    planned = plan_dedicated_file("# user-owned rule\n", "Managed rule", guide_version="0.5.0")

    assert planned.status is IntegrationStatus.CONFLICT


def test_dedicated_file_keeps_cursor_yaml_frontmatter_before_ownership_marker() -> None:
    frontmatter = "---\ndescription: Local Telegram archive workflow\nalwaysApply: true\n---\n"
    rendered = render_dedicated_file("Use cited local retrieval.", guide_version="0.5.0", frontmatter=frontmatter)
    refreshed = plan_dedicated_file(
        rendered,
        "Use newer cited local retrieval.",
        guide_version="0.5.1",
        action="refresh",
        frontmatter=frontmatter,
    )
    removed = plan_dedicated_file(
        refreshed.content,
        "Use newer cited local retrieval.",
        guide_version="0.5.1",
        action="uninstall",
        frontmatter=frontmatter,
    )

    assert rendered.startswith(frontmatter + "<!-- tg-recall:begin")
    assert refreshed.status is IntegrationStatus.UPDATED
    assert refreshed.content is not None and refreshed.content.startswith(frontmatter + "<!-- tg-recall:begin")
    assert removed.status is IntegrationStatus.UPDATED
    assert removed.content is None


def test_dedicated_file_rejects_marker_before_or_mismatched_yaml_frontmatter() -> None:
    frontmatter = "---\ndescription: Local workflow\n---\n"
    marker_first = render_marked_block("Managed", guide_version="0.5.0") + frontmatter
    rendered = render_dedicated_file("Managed", guide_version="0.5.0", frontmatter=frontmatter)
    edited_frontmatter = rendered.replace("Local workflow", "Edited workflow")

    assert (
        plan_dedicated_file(marker_first, "Managed", guide_version="0.5.0", frontmatter=frontmatter).status
        is IntegrationStatus.CONFLICT
    )
    assert (
        plan_dedicated_file(edited_frontmatter, "Managed", guide_version="0.5.0", frontmatter=frontmatter).status
        is IntegrationStatus.CONFLICT
    )


def test_dedicated_file_state_digest_allows_only_exact_legacy_content() -> None:
    legacy = "---\ndescription: previously owned complete rule\n---\nUse cited retrieval.\n"
    digest = integration_files._sha256(legacy.encode("utf-8"))

    refreshed = plan_dedicated_file(legacy, "Managed", guide_version="0.5.0", action="refresh", owned_digest=digest)
    edited = plan_dedicated_file(
        legacy + "User edit\n", "Managed", guide_version="0.5.0", action="refresh", owned_digest=digest
    )

    assert refreshed.status is IntegrationStatus.UPDATED
    assert edited.status is IntegrationStatus.CONFLICT


def test_json_merge_rejects_duplicates_and_unowned_conflicts() -> None:
    duplicate = merge_json_mcp('{"mcpServers": {}, "mcpServers": {}}', {"command": "tg-recall-mcp"})
    conflict = merge_json_mcp('{"mcpServers": {"tg-recall": {"command": "other"}}}', {"command": "tg-recall-mcp"})

    assert duplicate.status is IntegrationStatus.CONFLICT
    assert conflict.status is IntegrationStatus.CONFLICT


def test_json_merge_is_idempotent_and_preserves_unrelated_entries() -> None:
    existing = '{"other": 1, "mcpServers": {"unrelated": {"command": "keep"}}}'
    first = merge_json_mcp(existing, {"command": "tg-recall-mcp", "args": []})
    second = merge_json_mcp(first.content, {"args": [], "command": "tg-recall-mcp"})

    assert first.status is IntegrationStatus.INSTALLED
    assert json.loads(first.content) == {
        "other": 1,
        "mcpServers": {
            "unrelated": {"command": "keep"},
            "tg-recall": {"command": "tg-recall-mcp", "args": []},
        },
    }
    assert second.status is IntegrationStatus.UNCHANGED


def test_owned_json_entry_can_refresh_or_uninstall_only_with_state_proof() -> None:
    existing = '{"mcpServers": {"tg-recall": {"command": "old"}, "other": {"command": "keep"}}}'

    refreshed = merge_json_mcp(existing, {"command": "tg-recall-mcp"}, action="refresh", owned=True)
    removed = merge_json_mcp(refreshed.content, {"command": "tg-recall-mcp"}, action="uninstall", owned=True)

    assert refreshed.status is IntegrationStatus.UPDATED
    assert json.loads(removed.content) == {"mcpServers": {"other": {"command": "keep"}}}


def test_toml_block_preserves_unrelated_content_and_rejects_unowned_table() -> None:
    body = '[mcp_servers.tg-recall]\ncommand = "tg-recall-mcp"'
    first = apply_owned_toml_block('title = "keep"\n', body, guide_version="0.5.0")
    second = apply_owned_toml_block(first.content, body, guide_version="0.5.0")
    conflict = apply_owned_toml_block('[mcp_servers.tg-recall]\ncommand = "other"\n', body, guide_version="0.5.0")

    assert first.status is IntegrationStatus.INSTALLED
    assert first.content is not None and 'title = "keep"' in first.content
    assert second.status is IntegrationStatus.UNCHANGED
    assert conflict.status is IntegrationStatus.CONFLICT


def test_target_validation_rejects_escape_and_symlink(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()

    with pytest.raises(IntegrationPathError, match="remain below"):
        validate_integration_target(root / ".." / "outside.json", root=root)

    outside = tmp_path / "outside"
    outside.mkdir()
    linked = root / "linked"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - Windows privilege policy varies
        pytest.skip(f"symlinks unavailable: {exc}")
    with pytest.raises(IntegrationPathError, match="symlink or reparse"):
        validate_integration_target(linked / "config.json", root=root)


def test_ensure_integration_directory_previews_and_creates_private_nested_directory(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()

    preview = ensure_integration_directory(".cursor/rules", root=root, apply=False)
    assert preview.path == root / ".cursor" / "rules"
    assert preview.preview is True and preview.created is False
    assert not preview.path.exists()

    applied = ensure_integration_directory(".cursor/rules", root=root, apply=True)
    repeated = ensure_integration_directory(".cursor/rules", root=root, apply=True)

    assert applied.path.is_dir() and applied.created is True and applied.preview is False
    assert repeated.created is False and repeated.preview is False
    if os.name != "nt":
        assert stat.S_IMODE(applied.path.stat().st_mode) & 0o077 == 0


def test_ensure_integration_directory_rejects_symlinked_parent(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = root / ".cursor"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - Windows privilege policy varies
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(IntegrationPathError, match="symlink or reparse"):
        ensure_integration_directory(".cursor/rules", root=root, apply=True)


def test_atomic_replace_creates_private_adjacent_backup(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "config.json"
    target.write_text("old", encoding="utf-8")

    mutation = apply_file_change(target, PlannedFileChange(IntegrationStatus.UPDATED, "new"), root=root)

    assert mutation.changed is True
    assert target.read_text(encoding="utf-8") == "new"
    assert mutation.backup is not None
    assert mutation.backup.parent == target.parent
    assert mutation.backup.read_text(encoding="utf-8") == "old"
    if os.name != "nt":
        assert stat.S_IMODE(mutation.backup.stat().st_mode) & 0o077 == 0


def test_windows_private_files_use_existing_acl_hardener(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "config.json"
    target.write_text("private", encoding="utf-8")
    calls: list[tuple[Path, bool]] = []

    def hardened(path: Path, *, is_dir: bool) -> SecurityFinding:
        calls.append((Path(path), is_dir))
        return SecurityFinding(str(path), "ok", "restricted")

    monkeypatch.setattr(integration_files.os, "name", "nt")
    monkeypatch.setattr(integration_files, "harden_path", hardened)
    integration_files._make_private(target, is_dir=False)

    assert calls == [(target, False)]


def test_noop_does_not_create_backup(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "config.json"
    target.write_text("same", encoding="utf-8")

    mutation = apply_file_change(target, PlannedFileChange(IntegrationStatus.UNCHANGED, "same"), root=root)

    assert mutation.changed is False
    assert mutation.backup is None
    assert not list(root.glob("*.tg-recall-backup-*"))


def test_atomic_failure_keeps_original_and_recovery_backup(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "config.json"
    target.write_text("old", encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(integration_files.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        apply_file_change(target, PlannedFileChange(IntegrationStatus.UPDATED, "new"), root=root)

    assert target.read_text(encoding="utf-8") == "old"
    assert len(list(root.glob(".*.tg-recall-backup-*.bak"))) == 1
    assert not list(root.glob(".*.tg-recall-*.tmp"))


def test_minimal_ownership_state_has_no_profile_dependency(tmp_path: Path) -> None:
    record = ownership_record(
        harness="codex",
        scope=IntegrationScope.PROJECT,
        component=IntegrationComponent.MCP,
        target=tmp_path / "config.toml",
        guide_version="0.5.0",
        content="public integration fragment",
    )

    path = save_ownership_state(tmp_path, [record])
    loaded = load_ownership_state(tmp_path)

    assert path.name == "integration-ownership.json"
    assert loaded == (record,)


def test_conflict_plan_never_mutates_target(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "AGENTS.md"
    target.write_text("user content", encoding="utf-8")
    conflict = PlannedFileChange(IntegrationStatus.CONFLICT, conflict="edited marker")

    with pytest.raises(IntegrationConflictError, match="edited marker"):
        apply_file_change(target, conflict, root=root)
    assert target.read_text(encoding="utf-8") == "user content"
