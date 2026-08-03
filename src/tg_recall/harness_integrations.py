"""Documented, profile-free lifecycle adapters for AI harnesses.

The module maps a versioned :class:`AgentGuide` to documented harness files.
It deliberately contains no CLI parsing, config loading, Telegram access, or
client subprocess execution.  All file ownership and path safety are delegated
to :mod:`tg_recall.integration_files`.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterable

from .agent_routing import AgentGuide, HarnessTarget, render_harness_instruction
from .integration_files import (
    HarnessCapability,
    FileMutation,
    IntegrationComponent,
    IntegrationComponentResult,
    IntegrationConflictError,
    IntegrationFileError,
    IntegrationPathError,
    IntegrationResult,
    IntegrationScope,
    IntegrationStatus,
    OwnershipRecord,
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
    rollback_file_mutation,
    save_ownership_state,
    validate_integration_target,
)


class IntegrationAction(StrEnum):
    PREVIEW = "preview"
    INSTALL = "install"
    STATUS = "status"
    REFRESH = "refresh"
    UNINSTALL = "uninstall"


class _ArtifactKind(StrEnum):
    MARKDOWN = "markdown"
    CURSOR_RULE = "cursor_rule"
    JSON_MCP = "json_mcp"
    TOML_MCP = "toml_mcp"
    MANUAL = "manual"


ALL_HARNESSES = "all"
DOCUMENTED_HARNESSES = (HarnessTarget.CODEX, HarnessTarget.CLAUDE_CODE, HarnessTarget.CURSOR)
MCP_SERVER = {"command": "tg-recall-mcp", "args": []}
CODEX_MCP_TOML = '[mcp_servers.tg-recall]\ncommand = "tg-recall-mcp"\nargs = []'
CURSOR_FRONTMATTER = "---\ndescription: tg-recall local cited Telegram workflow\nalwaysApply: true\n---"


@dataclass(frozen=True)
class IntegrationLocations:
    """Explicit public roots only; none refer to a tg-recall profile."""

    project_root: Path | None = None
    user_home: Path | None = None
    generic_root: Path | None = None
    generic_instruction_target: Path | None = None
    generic_mcp_target: Path | None = None
    state_root: Path | None = None


@dataclass(frozen=True)
class _ComponentPlan:
    harness: HarnessTarget
    scope: IntegrationScope
    component: IntegrationComponent
    kind: _ArtifactKind
    root: Path | None = None
    target: Path | None = None
    parent_relative: Path | None = None
    manual_action: str | None = None


@dataclass(frozen=True)
class _Preflight:
    plan: _ComponentPlan
    content: str | None
    change: PlannedFileChange | None
    result: IntegrationComponentResult

    @property
    def should_apply(self) -> bool:
        return self.result.status in {IntegrationStatus.INSTALLED, IntegrationStatus.UPDATED}


def harness_capabilities() -> tuple[HarnessCapability, ...]:
    """Return the complete documented capability matrix in stable order."""

    return default_capabilities()


def run_harness_lifecycle(
    action: IntegrationAction | str,
    targets: Iterable[HarnessTarget | str],
    scope: IntegrationScope | str,
    guide: AgentGuide,
    *,
    locations: IntegrationLocations,
) -> IntegrationResult:
    """Plan or apply one explicit scoped harness lifecycle operation.

    ``preview`` and ``status`` never mutate files.  ``refresh`` only changes
    currently managed artifacts; an absent artifact is reported as partial.
    Generic destinations must be explicitly supplied below ``generic_root``.
    """

    selected_action = IntegrationAction(action)
    selected_scope = IntegrationScope(scope)
    selected_targets = _expand_targets(targets)
    if locations.state_root is not None and not Path(locations.state_root).is_dir():
        raise IntegrationPathError("integration state root must be an existing directory")
    records = _load_records(locations.state_root)
    preflights: list[_Preflight] = []

    for target in selected_targets:
        for plan in _component_plans(target, selected_scope, locations):
            preflights.append(_preflight_component(selected_action, plan, guide, records))

    if selected_action not in {IntegrationAction.INSTALL, IntegrationAction.REFRESH, IntegrationAction.UNINSTALL}:
        return _lifecycle_result(selected_action, selected_targets, selected_scope, guide, [item.result for item in preflights])

    if any(item.result.status is IntegrationStatus.CONFLICT for item in preflights):
        return _lifecycle_result(
            selected_action,
            selected_targets,
            selected_scope,
            guide,
            _aborted_results(preflights, "not applied because another integration component conflicted"),
        )

    applying = [item for item in preflights if item.should_apply]
    if not applying:
        return _lifecycle_result(selected_action, selected_targets, selected_scope, guide, [item.result for item in preflights])

    if locations.state_root is None:
        return _lifecycle_result(
            selected_action,
            selected_targets,
            selected_scope,
            guide,
            _aborted_results(preflights, "integration ownership state is required before mutation"),
        )

    mutations: list[tuple[_Preflight, FileMutation]] = []
    try:
        for item in applying:
            mutations.append((item, _apply_preflight(item)))
    except (IntegrationFileError, OSError, UnicodeError) as exc:
        rollback_error = _rollback_mutations(mutations)
        reason = f"integration mutation failed: {exc}"
        if rollback_error:
            reason = f"{reason}; rollback failed: {rollback_error}"
        return _lifecycle_result(
            selected_action,
            selected_targets,
            selected_scope,
            guide,
            _aborted_results(preflights, reason, aborted_status=IntegrationStatus.CONFLICT),
        )

    committed_records = _stage_records(records, mutations, selected_action, guide)
    try:
        # The state commit occurs only after all harness files succeeded, and
        # records include the concrete backups produced by those mutations.
        save_ownership_state(locations.state_root, committed_records)
    except (IntegrationFileError, OSError) as exc:
        rollback_error = _rollback_mutations(mutations)
        reason = f"ownership state commit failed: {exc}"
        if rollback_error:
            reason = f"{reason}; rollback failed: {rollback_error}"
        return _lifecycle_result(
            selected_action,
            selected_targets,
            selected_scope,
            guide,
            _aborted_results(preflights, reason, aborted_status=IntegrationStatus.CONFLICT),
        )

    changed = {id(item): mutation for item, mutation in mutations}
    components = [
        _committed_component(item, changed.get(id(item)))
        for item in preflights
    ]
    return _lifecycle_result(selected_action, selected_targets, selected_scope, guide, components)


def _expand_targets(targets: Iterable[HarnessTarget | str]) -> tuple[HarnessTarget, ...]:
    expanded: list[HarnessTarget] = []
    for raw in targets:
        if str(raw) == ALL_HARNESSES:
            expanded.extend(DOCUMENTED_HARNESSES)
        else:
            expanded.append(HarnessTarget(raw))
    if not expanded:
        raise ValueError("at least one integration harness is required")
    return tuple(sorted(set(expanded), key=lambda item: item.value))


def _component_plans(
    harness: HarnessTarget,
    scope: IntegrationScope,
    locations: IntegrationLocations,
) -> tuple[_ComponentPlan, ...]:
    if harness is HarnessTarget.CODEX:
        if scope is IntegrationScope.PROJECT:
            root = _require_root(locations.project_root, "project root")
            return (
                _file_plan(harness, scope, IntegrationComponent.INSTRUCTIONS, _ArtifactKind.MARKDOWN, root, Path("AGENTS.md")),
                _file_plan(harness, scope, IntegrationComponent.MCP, _ArtifactKind.TOML_MCP, root, Path(".codex/config.toml")),
            )
        root = _require_root(locations.user_home, "user home")
        return (
            _file_plan(harness, scope, IntegrationComponent.INSTRUCTIONS, _ArtifactKind.MARKDOWN, root, Path(".codex/AGENTS.md")),
            _file_plan(harness, scope, IntegrationComponent.MCP, _ArtifactKind.TOML_MCP, root, Path(".codex/config.toml")),
        )

    if harness is HarnessTarget.CLAUDE_CODE:
        if scope is IntegrationScope.PROJECT:
            root = _require_root(locations.project_root, "project root")
            return (
                _file_plan(harness, scope, IntegrationComponent.INSTRUCTIONS, _ArtifactKind.MARKDOWN, root, Path("CLAUDE.md")),
                _file_plan(harness, scope, IntegrationComponent.MCP, _ArtifactKind.JSON_MCP, root, Path(".mcp.json")),
            )
        root = _require_root(locations.user_home, "user home")
        return (
            _file_plan(harness, scope, IntegrationComponent.INSTRUCTIONS, _ArtifactKind.MARKDOWN, root, Path(".claude/CLAUDE.md")),
            _manual_plan(harness, scope, IntegrationComponent.MCP, "Run `claude mcp add --scope user tg-recall -- tg-recall-mcp` or add the server manually."),
        )

    if harness is HarnessTarget.CURSOR:
        if scope is IntegrationScope.PROJECT:
            root = _require_root(locations.project_root, "project root")
            return (
                _file_plan(harness, scope, IntegrationComponent.INSTRUCTIONS, _ArtifactKind.CURSOR_RULE, root, Path(".cursor/rules/tg-recall.mdc")),
                _file_plan(harness, scope, IntegrationComponent.MCP, _ArtifactKind.JSON_MCP, root, Path(".cursor/mcp.json")),
            )
        root = _require_root(locations.user_home, "user home")
        return (
            _manual_plan(harness, scope, IntegrationComponent.INSTRUCTIONS, "Add the generated Cursor User Rule in Cursor Settings > Rules manually."),
            _file_plan(harness, scope, IntegrationComponent.MCP, _ArtifactKind.JSON_MCP, root, Path(".cursor/mcp.json")),
        )

    root = _require_root(locations.generic_root, "generic output root")
    return (
        _generic_plan(harness, scope, IntegrationComponent.INSTRUCTIONS, _ArtifactKind.MARKDOWN, root, locations.generic_instruction_target),
        _generic_plan(harness, scope, IntegrationComponent.MCP, _ArtifactKind.JSON_MCP, root, locations.generic_mcp_target),
    )


def _file_plan(
    harness: HarnessTarget,
    scope: IntegrationScope,
    component: IntegrationComponent,
    kind: _ArtifactKind,
    root: Path,
    relative_target: Path,
) -> _ComponentPlan:
    return _ComponentPlan(harness, scope, component, kind, root=root, target=root / relative_target, parent_relative=relative_target.parent)


def _generic_plan(
    harness: HarnessTarget,
    scope: IntegrationScope,
    component: IntegrationComponent,
    kind: _ArtifactKind,
    root: Path,
    target: Path | None,
) -> _ComponentPlan:
    if target is None:
        return _manual_plan(harness, scope, component, f"Provide an explicit generic {component.value} destination below the generic output root.")
    resolved = _absolute_target(root, target)
    try:
        relative = resolved.relative_to(_absolute_target(root, Path(".")))
    except ValueError as exc:
        raise IntegrationPathError("generic destination must remain below the explicit output root") from exc
    return _ComponentPlan(harness, scope, component, kind, root=root, target=resolved, parent_relative=relative.parent)


def _manual_plan(harness: HarnessTarget, scope: IntegrationScope, component: IntegrationComponent, manual_action: str) -> _ComponentPlan:
    return _ComponentPlan(harness, scope, component, _ArtifactKind.MANUAL, manual_action=manual_action)


def _preflight_component(
    action: IntegrationAction,
    plan: _ComponentPlan,
    guide: AgentGuide,
    records: tuple[OwnershipRecord, ...],
) -> _Preflight:
    if plan.kind is _ArtifactKind.MANUAL:
        return _Preflight(
            plan,
            None,
            None,
            IntegrationComponentResult(
                harness=plan.harness.value,
                scope=plan.scope,
                component=plan.component,
                status=IntegrationStatus.MANUAL_REQUIRED,
                manual_action=plan.manual_action,
            ),
        )

    assert plan.root is not None and plan.target is not None and plan.parent_relative is not None
    try:
        content = _read_existing_content_for_preflight(plan)
        record = _matching_record(plan, records)
        owned = _is_owned(plan, content, record)
        change = _plan_change(action, plan, guide, content, owned, record.digest if owned and record is not None else None)
        status, warning = _effective_status(action, change)
        return _Preflight(
            plan,
            content,
            change,
            IntegrationComponentResult(
                harness=plan.harness.value,
                scope=plan.scope,
                component=plan.component,
                status=status,
                target=str(plan.target),
                conflict=change.conflict,
                warning=warning,
            ),
        )
    except (IntegrationFileError, OSError, UnicodeError) as exc:
        return _Preflight(
            plan,
            None,
            None,
            IntegrationComponentResult(
                harness=plan.harness.value,
                scope=plan.scope,
                component=plan.component,
                status=IntegrationStatus.CONFLICT,
                target=str(plan.target),
                conflict=str(exc),
            ),
        )


def _read_existing_content_for_preflight(plan: _ComponentPlan) -> str | None:
    assert plan.root is not None and plan.target is not None and plan.parent_relative is not None
    parent_exists = (plan.root / plan.parent_relative).is_dir()
    if not parent_exists:
        ensure_integration_directory(plan.parent_relative, root=plan.root, apply=False)
        return None
    target = validate_integration_target(plan.target, root=plan.root)
    return target.read_text(encoding="utf-8") if target.exists() else None


def _apply_preflight(item: _Preflight):
    assert item.plan.root is not None and item.plan.target is not None and item.plan.parent_relative is not None
    assert item.change is not None
    if not (item.plan.root / item.plan.parent_relative).is_dir():
        ensure_integration_directory(item.plan.parent_relative, root=item.plan.root, apply=True)
    return apply_file_change(item.plan.target, item.change, root=item.plan.root)


def _stage_records(
    records: tuple[OwnershipRecord, ...],
    mutations: list[tuple[_Preflight, FileMutation]],
    action: IntegrationAction,
    guide: AgentGuide,
) -> list[OwnershipRecord]:
    staged = list(records)
    for item, mutation in mutations:
        assert item.change is not None and item.plan.target is not None
        if action is IntegrationAction.UNINSTALL:
            _replace_record(staged, _deleted_record(item.plan))
            continue
        _replace_record(
            staged,
            ownership_record(
                harness=item.plan.harness.value,
                scope=item.plan.scope,
                component=item.plan.component,
                target=item.plan.target,
                guide_version=guide.prompt_version,
                content=_ownership_content(item.plan, item.change.content or ""),
                backup=mutation.backup,
            ),
        )
    return staged


def _rollback_mutations(mutations: list[tuple[_Preflight, FileMutation]]) -> str | None:
    errors: list[str] = []
    for item, mutation in reversed(mutations):
        assert item.plan.root is not None
        try:
            rollback_file_mutation(mutation, root=item.plan.root)
        except (IntegrationFileError, OSError) as exc:
            errors.append(str(exc))
    return "; ".join(errors) if errors else None


def _committed_component(item: _Preflight, mutation: FileMutation | None) -> IntegrationComponentResult:
    if mutation is None:
        return item.result
    return IntegrationComponentResult(
        harness=item.result.harness,
        scope=item.result.scope,
        component=item.result.component,
        status=item.result.status,
        target=item.result.target,
        changed_path=str(mutation.target) if mutation.changed else None,
        backup_path=str(mutation.backup) if mutation.backup else None,
        warning=item.result.warning,
    )


def _aborted_results(
    preflights: list[_Preflight],
    reason: str,
    *,
    aborted_status: IntegrationStatus = IntegrationStatus.PARTIAL,
) -> list[IntegrationComponentResult]:
    results: list[IntegrationComponentResult] = []
    for item in preflights:
        if item.result.status is IntegrationStatus.CONFLICT:
            results.append(item.result)
        elif item.should_apply:
            results.append(
                IntegrationComponentResult(
                    harness=item.result.harness,
                    scope=item.result.scope,
                    component=item.result.component,
                    status=aborted_status,
                    target=item.result.target,
                    warning=reason,
                )
            )
        else:
            results.append(item.result)
    return results


def _lifecycle_result(
    action: IntegrationAction,
    targets: tuple[HarnessTarget, ...],
    scope: IntegrationScope,
    guide: AgentGuide,
    components: list[IntegrationComponentResult],
) -> IntegrationResult:
    return IntegrationResult(
        action=action.value,
        requested_harnesses=tuple(target.value for target in targets),
        scope=scope,
        installed_version=guide.tg_recall_version,
        components=tuple(components),
    )


def _plan_change(
    action: IntegrationAction,
    plan: _ComponentPlan,
    guide: AgentGuide,
    content: str | None,
    owned: bool,
    owned_digest: str | None,
) -> PlannedFileChange:
    primitive_action = action.value if action in {IntegrationAction.REFRESH, IntegrationAction.UNINSTALL} else "install"
    if plan.kind is _ArtifactKind.MARKDOWN:
        return apply_marked_block(content, render_harness_instruction(guide, plan.harness), guide_version=guide.prompt_version, action=primitive_action)
    if plan.kind is _ArtifactKind.CURSOR_RULE:
        body = _cursor_body(guide)
        return plan_dedicated_file(
            content,
            body,
            guide_version=guide.prompt_version,
            action=primitive_action,
            frontmatter=CURSOR_FRONTMATTER,
            owned_digest=owned_digest,
        )
    if plan.kind is _ArtifactKind.JSON_MCP:
        return merge_json_mcp(content, MCP_SERVER, action=primitive_action, owned=owned)
    if plan.kind is _ArtifactKind.TOML_MCP:
        return apply_owned_toml_block(content, CODEX_MCP_TOML, guide_version=guide.prompt_version, action=primitive_action)
    raise AssertionError(f"unsupported artifact kind: {plan.kind}")


def _effective_status(action: IntegrationAction, change: PlannedFileChange) -> tuple[IntegrationStatus, str | None]:
    if change.status is IntegrationStatus.CONFLICT:
        return IntegrationStatus.CONFLICT, None
    if action is IntegrationAction.STATUS:
        if change.status is IntegrationStatus.UNCHANGED:
            return IntegrationStatus.UNCHANGED, None
        if change.status is IntegrationStatus.INSTALLED:
            return IntegrationStatus.PARTIAL, "component is not installed"
        return IntegrationStatus.PARTIAL, "managed component requires refresh"
    if action is IntegrationAction.REFRESH and change.status is IntegrationStatus.INSTALLED:
        return IntegrationStatus.PARTIAL, "refresh only updates an already managed component"
    return change.status, None


def _cursor_body(guide: AgentGuide) -> str:
    rendered = render_harness_instruction(guide, HarnessTarget.CURSOR)
    prefix = CURSOR_FRONTMATTER + "\n"
    if not rendered.startswith(prefix):
        raise IntegrationConflictError("Cursor renderer does not expose the required front matter")
    return rendered[len(prefix) :]


def _load_records(state_root: Path | None) -> tuple[OwnershipRecord, ...]:
    return () if state_root is None else load_ownership_state(state_root)


def _is_owned(plan: _ComponentPlan, content: str | None, record: OwnershipRecord | None) -> bool:
    if record is None or content is None:
        return False
    return record.digest == _digest(_ownership_content(plan, content))


def _matching_record(plan: _ComponentPlan, records: tuple[OwnershipRecord, ...]) -> OwnershipRecord | None:
    assert plan.target is not None
    selected = str(plan.target)
    return next(
        (
            record
            for record in records
            if record.harness == plan.harness.value
            and record.scope is plan.scope
            and record.component is plan.component
            and record.target == selected
        ),
        None,
    )


def _ownership_content(plan: _ComponentPlan, content: str) -> str:
    if plan.kind is _ArtifactKind.JSON_MCP:
        try:
            document = json.loads(content)
            return json.dumps(document["mcpServers"]["tg-recall"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (KeyError, TypeError, json.JSONDecodeError):
            return content
    return content


def _deleted_record(plan: _ComponentPlan) -> OwnershipRecord:
    assert plan.target is not None
    return OwnershipRecord(
        harness=plan.harness.value,
        scope=plan.scope,
        component=plan.component,
        target=str(plan.target),
        guide_version="",
        digest="",
        updated_at="",
    )


def _replace_record(records: list[OwnershipRecord], record: OwnershipRecord) -> None:
    records[:] = [
        item
        for item in records
        if not (
            item.harness == record.harness
            and item.scope is record.scope
            and item.component is record.component
            and item.target == record.target
        )
    ]
    if record.digest:
        records.append(record)


def _require_root(path: Path | None, label: str) -> Path:
    if path is None:
        raise IntegrationPathError(f"{label} is required")
    return Path(os.path.abspath(path))


def _absolute_target(root: Path, target: Path) -> Path:
    raw = target.expanduser()
    selected = raw if raw.is_absolute() else root / raw
    return Path(os.path.abspath(selected))


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
