from __future__ import annotations

import json
from pathlib import Path

from tg_recall.agent_routing import HarnessTarget, build_agent_guide
from tg_recall.harness_integrations import (
    ALL_HARNESSES,
    IntegrationAction,
    IntegrationLocations,
    harness_capabilities,
    run_harness_lifecycle,
)
from tg_recall.integration_files import IntegrationScope, IntegrationStatus
from tg_recall.integration_files import IntegrationPathError


def _locations(tmp_path: Path, *, generic: bool = True) -> IntegrationLocations:
    project = tmp_path / "project"
    user_home = tmp_path / "home"
    state = tmp_path / "state"
    project.mkdir()
    user_home.mkdir()
    state.mkdir()
    if not generic:
        return IntegrationLocations(project_root=project, user_home=user_home, state_root=state)
    generic_root = tmp_path / "generic-output"
    generic_root.mkdir()
    return IntegrationLocations(
        project_root=project,
        user_home=user_home,
        generic_root=generic_root,
        generic_instruction_target=Path("instructions.md"),
        generic_mcp_target=Path("mcp.json"),
        state_root=state,
    )


def _component(result: dict[str, object], harness: str, component: str) -> dict[str, object]:
    return next(
        item
        for item in result["components"]
        if item["harness"] == harness and item["component"] == component
    )


def test_capability_matrix_is_documented_and_honest() -> None:
    capabilities = {(item.harness, item.scope.value, item.component.value): item for item in harness_capabilities()}

    assert len(capabilities) == 16
    assert capabilities[("codex", "user", "instructions")].supported is True
    assert capabilities[("claude-code", "user", "mcp")].supported is False
    assert capabilities[("cursor", "user", "instructions")].supported is False
    assert capabilities[("cursor", "user", "mcp")].supported is True
    assert capabilities[("generic", "project", "instructions")].supported is True


def test_project_all_install_is_idempotent_and_uses_exact_documented_paths(tmp_path: Path) -> None:
    locations = _locations(tmp_path, generic=False)
    guide = build_agent_guide("0.5.0")

    first = run_harness_lifecycle(IntegrationAction.INSTALL, [ALL_HARNESSES], IntegrationScope.PROJECT, guide, locations=locations)
    first_payload = first.as_json()
    assert first_payload["status"] == IntegrationStatus.INSTALLED.value
    assert first_payload["requested_harnesses"] == ["claude-code", "codex", "cursor"]

    project = locations.project_root
    assert project is not None
    assert "tg-recall:begin" in (project / "AGENTS.md").read_text(encoding="utf-8")
    assert "[mcp_servers.tg-recall]" in (project / ".codex/config.toml").read_text(encoding="utf-8")
    assert "tg-recall:begin" in (project / "CLAUDE.md").read_text(encoding="utf-8")
    assert '"tg-recall"' in (project / ".mcp.json").read_text(encoding="utf-8")
    cursor_rule = (project / ".cursor/rules/tg-recall.mdc").read_text(encoding="utf-8")
    assert cursor_rule.startswith("---\ndescription: tg-recall local cited Telegram workflow\nalwaysApply: true\n---\n<!-- tg-recall:begin")
    assert '"tg-recall"' in (project / ".cursor/mcp.json").read_text(encoding="utf-8")
    second = run_harness_lifecycle(IntegrationAction.INSTALL, [ALL_HARNESSES], IntegrationScope.PROJECT, guide, locations=locations)
    assert second.as_json()["status"] == IntegrationStatus.UNCHANGED.value
    assert second.as_json()["changed_paths"] == []


def test_user_scope_installs_documented_codex_guidance_without_cursor_user_rule_write(tmp_path: Path) -> None:
    locations = _locations(tmp_path, generic=False)
    guide = build_agent_guide("0.5.0")
    result = run_harness_lifecycle(
        "install",
        [HarnessTarget.CODEX, HarnessTarget.CLAUDE_CODE, HarnessTarget.CURSOR],
        "user",
        guide,
        locations=locations,
    ).as_json()

    assert result["status"] == IntegrationStatus.PARTIAL.value
    assert _component(result, "codex", "instructions")["status"] == IntegrationStatus.INSTALLED.value
    assert _component(result, "claude-code", "mcp")["status"] == IntegrationStatus.MANUAL_REQUIRED.value
    assert _component(result, "cursor", "instructions")["status"] == IntegrationStatus.MANUAL_REQUIRED.value
    assert locations.user_home is not None
    assert "tg-recall:begin" in (locations.user_home / ".codex/AGENTS.md").read_text(encoding="utf-8")
    assert (locations.user_home / ".codex/config.toml").exists()
    assert (locations.user_home / ".claude/CLAUDE.md").exists()
    assert (locations.user_home / ".cursor/mcp.json").exists()
    assert not (locations.user_home / ".cursor/rules").exists()

    repeated = run_harness_lifecycle("install", [HarnessTarget.CODEX], "user", guide, locations=locations).as_json()
    assert repeated["status"] == IntegrationStatus.UNCHANGED.value


def test_refresh_updates_owned_artifacts_and_uninstall_preserves_unrelated_content(tmp_path: Path) -> None:
    locations = _locations(tmp_path, generic=False)
    project = locations.project_root
    assert project is not None
    (project / "AGENTS.md").write_text("keep this header\n", encoding="utf-8")
    old_guide = build_agent_guide("0.5.0")
    new_guide = build_agent_guide("0.6.0")

    run_harness_lifecycle("install", [HarnessTarget.CODEX, HarnessTarget.CURSOR], "project", old_guide, locations=locations)
    refreshed = run_harness_lifecycle("refresh", [HarnessTarget.CODEX, HarnessTarget.CURSOR], "project", new_guide, locations=locations).as_json()
    assert refreshed["status"] == IntegrationStatus.UPDATED.value
    assert "tg_recall_version: 0.6.0" in (project / "AGENTS.md").read_text(encoding="utf-8")
    assert "tg_recall_version: 0.6.0" in (project / ".cursor/rules/tg-recall.mdc").read_text(encoding="utf-8")
    assert locations.state_root is not None
    refreshed_state = json.loads((locations.state_root / "integration-ownership.json").read_text(encoding="utf-8"))
    assert any(record["backup_path"] for record in refreshed_state["records"])

    uninstalled = run_harness_lifecycle("uninstall", [HarnessTarget.CODEX, HarnessTarget.CURSOR], "project", new_guide, locations=locations).as_json()
    assert uninstalled["status"] == IntegrationStatus.UPDATED.value
    assert (project / "AGENTS.md").read_text(encoding="utf-8") == "keep this header\n"
    assert not (project / ".cursor/rules/tg-recall.mdc").exists()
    assert '"tg-recall"' not in (project / ".cursor/mcp.json").read_text(encoding="utf-8")


def test_preview_and_missing_generic_destinations_never_write(tmp_path: Path) -> None:
    locations = _locations(tmp_path, generic=False)
    preview = run_harness_lifecycle("preview", [HarnessTarget.CURSOR], "project", build_agent_guide("0.5.0"), locations=locations).as_json()
    assert preview["status"] == IntegrationStatus.INSTALLED.value
    assert locations.project_root is not None
    assert not (locations.project_root / ".cursor").exists()

    generic_root = tmp_path / "generic"
    generic_root.mkdir()
    incomplete = IntegrationLocations(generic_root=generic_root, state_root=locations.state_root)
    generic = run_harness_lifecycle("install", [HarnessTarget.GENERIC], "project", build_agent_guide("0.5.0"), locations=incomplete).as_json()
    assert generic["status"] == IntegrationStatus.PARTIAL.value
    assert all(item["status"] == IntegrationStatus.MANUAL_REQUIRED.value for item in generic["components"])
    assert list(generic_root.iterdir()) == []


def test_status_is_read_only_and_explicit_generic_can_return_mixed_partial_results(tmp_path: Path) -> None:
    locations = _locations(tmp_path, generic=False)
    project = locations.project_root
    assert project is not None

    missing = run_harness_lifecycle("status", [HarnessTarget.CURSOR], "project", build_agent_guide("0.5.0"), locations=locations).as_json()
    assert missing["status"] == IntegrationStatus.PARTIAL.value
    assert not (project / ".cursor").exists()

    run_harness_lifecycle("install", [HarnessTarget.CODEX], "project", build_agent_guide("0.5.0"), locations=locations)
    status = run_harness_lifecycle("status", [HarnessTarget.CODEX], "project", build_agent_guide("0.5.0"), locations=locations).as_json()
    assert status["status"] == IntegrationStatus.UNCHANGED.value
    assert status["changed_paths"] == []

    generic_root = tmp_path / "generic"
    generic_root.mkdir()
    mixed_locations = IntegrationLocations(project_root=project, generic_root=generic_root, state_root=locations.state_root)
    mixed = run_harness_lifecycle(
        "install",
        [ALL_HARNESSES, HarnessTarget.GENERIC],
        "project",
        build_agent_guide("0.5.0"),
        locations=mixed_locations,
    ).as_json()
    assert mixed["status"] == IntegrationStatus.PARTIAL.value
    assert _component(mixed, "generic", "instructions")["status"] == IntegrationStatus.MANUAL_REQUIRED.value
    assert _component(mixed, "generic", "mcp")["status"] == IntegrationStatus.MANUAL_REQUIRED.value


def test_missing_ownership_state_root_fails_before_harness_write(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    locations = IntegrationLocations(project_root=project, state_root=tmp_path / "missing-state")

    try:
        run_harness_lifecycle("install", [HarnessTarget.CODEX], "project", build_agent_guide("0.5.0"), locations=locations)
    except IntegrationPathError as exc:
        assert "state root" in str(exc)
    else:  # pragma: no cover - guard against an unsafe silent fallback
        raise AssertionError("missing state root must block lifecycle mutation")
    assert not (project / "AGENTS.md").exists()


def test_preflight_conflict_in_later_component_leaves_first_component_untouched(tmp_path: Path) -> None:
    locations = _locations(tmp_path, generic=False)
    project = locations.project_root
    assert project is not None
    (project / ".codex").mkdir()
    (project / ".codex/config.toml").write_text('[mcp_servers.tg-recall]\ncommand = "foreign-mcp"\n', encoding="utf-8")

    result = run_harness_lifecycle("install", [HarnessTarget.CODEX], "project", build_agent_guide("0.5.0"), locations=locations).as_json()
    assert result["status"] == IntegrationStatus.CONFLICT.value
    assert _component(result, "codex", "instructions")["status"] == IntegrationStatus.PARTIAL.value
    assert not (project / "AGENTS.md").exists()
    assert (project / ".codex/config.toml").read_text(encoding="utf-8") == '[mcp_servers.tg-recall]\ncommand = "foreign-mcp"\n'


def test_second_mutation_failure_rolls_back_first_mutation_and_preserves_state(tmp_path: Path, monkeypatch) -> None:
    locations = _locations(tmp_path, generic=False)
    calls = 0
    from tg_recall import harness_integrations

    original_apply = harness_integrations._apply_preflight

    def fail_second_apply(item):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated second mutation failure")
        return original_apply(item)

    monkeypatch.setattr(harness_integrations, "_apply_preflight", fail_second_apply)
    result = run_harness_lifecycle("install", [HarnessTarget.CODEX], "project", build_agent_guide("0.5.0"), locations=locations).as_json()

    assert result["status"] == IntegrationStatus.CONFLICT.value
    assert locations.project_root is not None
    assert not (locations.project_root / "AGENTS.md").exists()
    assert locations.state_root is not None
    assert not (locations.state_root / "integration-ownership.json").exists()


def test_state_commit_failure_rolls_back_successful_uninstall_and_keeps_old_json_ownership(tmp_path: Path, monkeypatch) -> None:
    locations = _locations(tmp_path, generic=False)
    guide = build_agent_guide("0.5.0")
    run_harness_lifecycle("install", [HarnessTarget.CURSOR], "project", guide, locations=locations)
    assert locations.project_root is not None and locations.state_root is not None
    mcp_path = locations.project_root / ".cursor/mcp.json"
    rule_path = locations.project_root / ".cursor/rules/tg-recall.mdc"
    before_mcp = mcp_path.read_text(encoding="utf-8")
    before_rule = rule_path.read_text(encoding="utf-8")
    before_state = (locations.state_root / "integration-ownership.json").read_text(encoding="utf-8")

    def fail_state_write(*_args, **_kwargs):
        raise OSError("simulated state commit failure")

    monkeypatch.setattr("tg_recall.harness_integrations.save_ownership_state", fail_state_write)
    result = run_harness_lifecycle("uninstall", [HarnessTarget.CURSOR], "project", guide, locations=locations).as_json()

    assert result["status"] == IntegrationStatus.CONFLICT.value
    assert mcp_path.read_text(encoding="utf-8") == before_mcp
    assert rule_path.read_text(encoding="utf-8") == before_rule
    assert (locations.state_root / "integration-ownership.json").read_text(encoding="utf-8") == before_state
