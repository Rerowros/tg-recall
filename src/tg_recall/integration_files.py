"""Small, fail-closed primitives for AI-harness integration files.

This module deliberately knows nothing about tg-recall profiles, Telegram
credentials, or harness-specific locations.  Adapters provide an explicit
root and target, render public instruction/configuration content, and use the
helpers below to plan and apply the smallest owned change.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Sequence

from .security import harden_path

try:  # Python 3.11+; kept local because TOML writing is intentionally manual.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - project requires Python 3.13
    tomllib = None  # type: ignore[assignment]


INTEGRATION_SCHEMA_VERSION = 1
OWNER = "tg-recall"
_MARKER_START = "<!-- tg-recall:begin"
_MARKER_END = "<!-- tg-recall:end owner=tg-recall -->"
_TOML_MARKER_START = "# tg-recall:begin"
_TOML_MARKER_END = "# tg-recall:end owner=tg-recall"
_MARKDOWN_BEGIN_RE = re.compile(
    r"<!-- tg-recall:begin owner=tg-recall schema=(?P<schema>\d+) guide=(?P<guide>[^\s>]+) "
    r"sha256=(?P<digest>[0-9a-f]{64}) -->"
)
_TOML_BEGIN_RE = re.compile(
    r"# tg-recall:begin owner=tg-recall schema=(?P<schema>\d+) guide=(?P<guide>[^\s]+) "
    r"sha256=(?P<digest>[0-9a-f]{64})"
)
_UNOWNED_TOML_SERVER_RE = re.compile(r"(?m)^\s*\[\s*mcp_servers\.tg-recall\s*\]\s*$")


class IntegrationStatus(StrEnum):
    INSTALLED = "installed"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    PARTIAL = "partial"
    MANUAL_REQUIRED = "manual_required"
    CONFLICT = "conflict"
    UNSUPPORTED = "unsupported"


class IntegrationScope(StrEnum):
    USER = "user"
    PROJECT = "project"


class IntegrationComponent(StrEnum):
    INSTRUCTIONS = "instructions"
    MCP = "mcp"


@dataclass(frozen=True)
class HarnessCapability:
    """Public capability declaration; adapters must not guess absent support."""

    harness: str
    scope: IntegrationScope
    component: IntegrationComponent
    supported: bool
    lifecycle: tuple[str, ...] = ("preview", "install", "status", "refresh", "uninstall")
    manual_action: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "harness": self.harness,
            "scope": self.scope.value,
            "component": self.component.value,
            "supported": self.supported,
            "lifecycle": list(self.lifecycle),
            "manual_action": self.manual_action,
        }


@dataclass(frozen=True)
class IntegrationComponentResult:
    harness: str
    scope: IntegrationScope
    component: IntegrationComponent
    status: IntegrationStatus
    target: str | None = None
    changed_path: str | None = None
    backup_path: str | None = None
    conflict: str | None = None
    warning: str | None = None
    manual_action: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "harness": self.harness,
            "scope": self.scope.value,
            "component": self.component.value,
            "status": self.status.value,
            "target": self.target,
            "changed_path": self.changed_path,
            "backup_path": self.backup_path,
            "conflict": self.conflict,
            "warning": self.warning,
            "manual_action": self.manual_action,
        }


@dataclass(frozen=True)
class IntegrationResult:
    """Stable, secret-free top-level payload for future CLI adapters."""

    action: str
    requested_harnesses: tuple[str, ...]
    scope: IntegrationScope
    installed_version: str
    components: tuple[IntegrationComponentResult, ...] = ()
    warnings: tuple[str, ...] = ()
    manual_actions: tuple[str, ...] = ()
    next_actions: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        components = sorted(
            self.components,
            key=lambda item: (item.harness, item.scope.value, item.component.value, item.target or ""),
        )
        statuses = {item.status for item in components}
        if IntegrationStatus.CONFLICT in statuses:
            overall = IntegrationStatus.CONFLICT
        elif IntegrationStatus.PARTIAL in statuses or IntegrationStatus.MANUAL_REQUIRED in statuses:
            overall = IntegrationStatus.PARTIAL
        elif IntegrationStatus.UPDATED in statuses:
            overall = IntegrationStatus.UPDATED
        elif IntegrationStatus.INSTALLED in statuses:
            overall = IntegrationStatus.INSTALLED
        elif IntegrationStatus.UNSUPPORTED in statuses:
            overall = IntegrationStatus.UNSUPPORTED
        else:
            overall = IntegrationStatus.UNCHANGED
        return {
            "schema_version": INTEGRATION_SCHEMA_VERSION,
            "action": self.action,
            "status": overall.value,
            "installed_version": self.installed_version,
            "requested_harnesses": sorted(self.requested_harnesses),
            "scope": self.scope.value,
            "components": [item.as_json() for item in components],
            "changed_paths": sorted(item.changed_path for item in components if item.changed_path),
            "backup_paths": sorted(item.backup_path for item in components if item.backup_path),
            "conflicts": sorted(item.conflict for item in components if item.conflict),
            "warnings": sorted(set(self.warnings) | {item.warning for item in components if item.warning}),
            "manual_actions": sorted(
                set(self.manual_actions) | {item.manual_action for item in components if item.manual_action}
            ),
            "next_actions": sorted(self.next_actions),
        }


@dataclass(frozen=True)
class OwnershipRecord:
    harness: str
    scope: IntegrationScope
    component: IntegrationComponent
    target: str
    guide_version: str
    digest: str
    updated_at: str
    backup_path: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "harness": self.harness,
            "scope": self.scope.value,
            "component": self.component.value,
            "target": self.target,
            "guide_version": self.guide_version,
            "digest": self.digest,
            "updated_at": self.updated_at,
            "backup_path": self.backup_path,
        }


@dataclass(frozen=True)
class PlannedFileChange:
    status: IntegrationStatus
    content: str | None = None
    conflict: str | None = None

    @property
    def changes_file(self) -> bool:
        return self.status in {IntegrationStatus.INSTALLED, IntegrationStatus.UPDATED}


@dataclass(frozen=True)
class FileMutation:
    target: Path
    backup: Path | None
    changed: bool


@dataclass(frozen=True)
class DirectoryMutation:
    path: Path
    created: bool
    preview: bool


class IntegrationFileError(ValueError):
    pass


class IntegrationConflictError(IntegrationFileError):
    pass


class IntegrationPathError(IntegrationFileError):
    pass


def default_capabilities() -> tuple[HarnessCapability, ...]:
    """Return the documented inventory, independent of local machine state."""

    rows = (
        ("codex", IntegrationScope.PROJECT, IntegrationComponent.INSTRUCTIONS, True, None),
        ("codex", IntegrationScope.PROJECT, IntegrationComponent.MCP, True, None),
        ("codex", IntegrationScope.USER, IntegrationComponent.INSTRUCTIONS, True, None),
        ("codex", IntegrationScope.USER, IntegrationComponent.MCP, True, None),
        ("claude-code", IntegrationScope.PROJECT, IntegrationComponent.INSTRUCTIONS, True, None),
        ("claude-code", IntegrationScope.PROJECT, IntegrationComponent.MCP, True, None),
        ("claude-code", IntegrationScope.USER, IntegrationComponent.INSTRUCTIONS, True, None),
        (
            "claude-code",
            IntegrationScope.USER,
            IntegrationComponent.MCP,
            False,
            "Use the documented Claude CLI or add MCP manually.",
        ),
        ("cursor", IntegrationScope.PROJECT, IntegrationComponent.INSTRUCTIONS, True, None),
        ("cursor", IntegrationScope.PROJECT, IntegrationComponent.MCP, True, None),
        (
            "cursor",
            IntegrationScope.USER,
            IntegrationComponent.INSTRUCTIONS,
            False,
            "Add the generated Cursor User Rule manually.",
        ),
        ("cursor", IntegrationScope.USER, IntegrationComponent.MCP, True, None),
        ("generic", IntegrationScope.PROJECT, IntegrationComponent.INSTRUCTIONS, True, None),
        ("generic", IntegrationScope.PROJECT, IntegrationComponent.MCP, True, None),
        ("generic", IntegrationScope.USER, IntegrationComponent.INSTRUCTIONS, True, None),
        ("generic", IntegrationScope.USER, IntegrationComponent.MCP, True, None),
    )
    return tuple(
        HarnessCapability(harness, scope, component, supported, manual_action=manual_action)
        for harness, scope, component, supported, manual_action in rows
    )


def validate_integration_target(target: str | Path, *, root: str | Path) -> Path:
    """Validate an existing-parent target below an explicit, non-link root."""

    root_path, canonical_root = _validated_root(root)

    raw_target = Path(target).expanduser()
    candidate = _absolute_path(raw_target if raw_target.is_absolute() else root_path / raw_target)
    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise IntegrationPathError("integration target must remain below the explicit root") from exc
    if candidate == root_path:
        raise IntegrationPathError("integration target must be a file below the explicit root")
    if not candidate.parent.exists() or not candidate.parent.is_dir():
        raise IntegrationPathError("integration target parent must already exist")
    _reject_reparse_path(candidate.parent)
    if candidate.exists() or candidate.is_symlink():
        _reject_reparse(candidate)
    # A normal lexical child can still resolve out through a link in a parent.
    try:
        candidate.parent.resolve(strict=True).relative_to(canonical_root)
    except ValueError as exc:
        raise IntegrationPathError("integration target resolves outside the explicit root") from exc
    return candidate


def ensure_integration_directory(
    relative_path: str | Path,
    *,
    root: str | Path,
    apply: bool,
) -> DirectoryMutation:
    """Safely create a harness directory beneath ``root`` or preview that work.

    Callers must pass a relative destination such as ``.cursor/rules``.  This
    keeps directory creation under the same symlink/reparse boundary as files
    and prevents adapters from implementing their own unsafe ``mkdir`` walk.
    """

    root_path, canonical_root = _validated_root(root)
    requested = Path(relative_path)
    if (
        requested.is_absolute()
        or requested.drive
        or not requested.parts
        or any(part in {"", ".", ".."} for part in requested.parts)
    ):
        raise IntegrationPathError("integration directory must be a non-empty relative path")
    candidate = _absolute_path(root_path / requested)
    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise IntegrationPathError("integration directory must remain below the explicit root") from exc
    if candidate.exists() or candidate.is_symlink():
        _reject_reparse(candidate)
        if not candidate.is_dir():
            raise IntegrationPathError("integration directory target must be a directory")
        try:
            candidate.resolve(strict=True).relative_to(canonical_root)
        except ValueError as exc:
            raise IntegrationPathError("integration directory resolves outside the explicit root") from exc
        return DirectoryMutation(path=candidate, created=False, preview=not apply)

    _reject_reparse_path(candidate.parent)
    if not apply:
        return DirectoryMutation(path=candidate, created=False, preview=True)

    current = root_path
    created = False
    for part in requested.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _reject_reparse(current)
            if not current.is_dir():
                raise IntegrationPathError("integration directory target must be a directory")
            continue
        try:
            current.mkdir()
            created = True
            _make_private(current, is_dir=True)
        except FileExistsError:
            _reject_reparse(current)
            if not current.is_dir():
                raise IntegrationPathError("integration directory target must be a directory")
    _reject_reparse_path(candidate)
    try:
        candidate.resolve(strict=True).relative_to(canonical_root)
    except ValueError as exc:  # defensive recheck after every mkdir
        raise IntegrationPathError("integration directory resolves outside the explicit root") from exc
    return DirectoryMutation(path=candidate, created=created, preview=False)


def apply_file_change(target: str | Path, change: PlannedFileChange, *, root: str | Path) -> FileMutation:
    """Write a planned replacement or removal only after the plan is conflict-free."""

    if change.status is IntegrationStatus.CONFLICT:
        raise IntegrationConflictError(change.conflict or "integration file conflict")
    resolved = validate_integration_target(target, root=root)
    if not change.changes_file:
        return FileMutation(target=resolved, backup=None, changed=False)
    if change.content is None:
        return _remove_owned_file(resolved, root=root)
    return _atomic_replace(resolved, change.content.encode("utf-8"), root=root)


def rollback_file_mutation(mutation: FileMutation, *, root: str | Path) -> None:
    """Safely restore one successful integration mutation during a rollback.

    The adapter never manipulates harness paths directly.  Existing targets are
    restored from their private adjacent backup; newly-created targets have no
    backup and are removed after the same target-boundary validation used for
    normal writes.
    """

    if not mutation.changed:
        return
    target = validate_integration_target(mutation.target, root=root)
    if mutation.backup is not None:
        backup = Path(mutation.backup)
        _reject_reparse(backup)
        if not backup.exists() or not backup.is_file():
            raise IntegrationPathError("rollback backup is missing")
        _atomic_replace(target, backup.read_bytes(), root=root, create_backup=False)
        return
    if target.exists():
        target.unlink()


def render_marked_block(body: str, *, guide_version: str) -> str:
    normalized = _normalize_body(body)
    digest = _sha256(normalized.encode("utf-8"))
    return (
        f"<!-- tg-recall:begin owner={OWNER} schema={INTEGRATION_SCHEMA_VERSION} guide={guide_version} sha256={digest} -->\n"
        f"{normalized}\n{_MARKER_END}\n"
    )


def apply_marked_block(
    content: str | None, body: str, *, guide_version: str, action: str = "install"
) -> PlannedFileChange:
    """Plan a single owned Markdown block change without touching unrelated text."""

    existing = content or ""
    desired = render_marked_block(body, guide_version=guide_version)
    try:
        span = _find_owned_block(existing, _MARKDOWN_BEGIN_RE, _MARKER_START, _MARKER_END)
    except IntegrationConflictError as exc:
        return PlannedFileChange(IntegrationStatus.CONFLICT, conflict=str(exc))
    if span is None:
        if action == "uninstall":
            return PlannedFileChange(IntegrationStatus.UNCHANGED, existing)
        separator = "" if not existing or existing.endswith("\n") else "\n"
        return PlannedFileChange(IntegrationStatus.INSTALLED, existing + separator + desired)
    start, end, current_body, marker = span
    if _sha256(current_body.encode("utf-8")) != marker["digest"]:
        return PlannedFileChange(IntegrationStatus.CONFLICT, conflict="owned instruction block was edited")
    if action == "uninstall":
        updated = existing[:start] + existing[end:]
        return PlannedFileChange(IntegrationStatus.UPDATED, updated or None)
    if existing[start:end] == desired:
        return PlannedFileChange(IntegrationStatus.UNCHANGED, existing)
    return PlannedFileChange(IntegrationStatus.UPDATED, existing[:start] + desired + existing[end:])


def render_dedicated_file(body: str, *, guide_version: str, frontmatter: str | None = None) -> str:
    """Render an owned dedicated file, keeping optional YAML frontmatter first."""

    prefix = _normalize_yaml_frontmatter(frontmatter) if frontmatter is not None else ""
    return prefix + render_marked_block(body, guide_version=guide_version)


def plan_dedicated_file(
    content: str | None,
    body: str,
    *,
    guide_version: str,
    action: str = "install",
    frontmatter: str | None = None,
    owned_digest: str | None = None,
) -> PlannedFileChange:
    """Plan a dedicated owned file without overwriting user content.

    Cursor rules need YAML frontmatter before the generated rule.  When it is
    supplied, the marker is required immediately after that exact frontmatter.
    ``owned_digest`` is the state-record fallback for a legacy whole-file
    artifact that has no marker; it is intentionally exact-content only.
    """

    desired = render_dedicated_file(body, guide_version=guide_version, frontmatter=frontmatter)
    prefix = _normalize_yaml_frontmatter(frontmatter) if frontmatter is not None else ""
    if content is None:
        if action == "uninstall":
            return PlannedFileChange(IntegrationStatus.UNCHANGED)
        return PlannedFileChange(IntegrationStatus.INSTALLED, desired)
    try:
        span = _find_owned_block(content, _MARKDOWN_BEGIN_RE, _MARKER_START, _MARKER_END)
    except IntegrationConflictError as exc:
        return PlannedFileChange(IntegrationStatus.CONFLICT, conflict=str(exc))
    if span is None:
        if owned_digest is not None and _sha256(content.encode("utf-8")) == owned_digest:
            if action == "uninstall":
                return PlannedFileChange(IntegrationStatus.UPDATED, None)
            if action == "refresh":
                return PlannedFileChange(IntegrationStatus.UPDATED, desired)
        return PlannedFileChange(IntegrationStatus.CONFLICT, conflict="dedicated target contains unowned content")
    start, end, current_body, marker = span
    if _sha256(current_body.encode("utf-8")) != marker["digest"]:
        return PlannedFileChange(IntegrationStatus.CONFLICT, conflict="owned instruction block was edited")
    if start != len(prefix) or content[:start] != prefix or end != len(content):
        return PlannedFileChange(
            IntegrationStatus.CONFLICT,
            conflict="dedicated target has unowned content or ownership marker is not immediately after YAML front matter",
        )
    if action == "uninstall":
        return PlannedFileChange(IntegrationStatus.UPDATED, None)
    if content == desired:
        return PlannedFileChange(IntegrationStatus.UNCHANGED, content)
    return PlannedFileChange(IntegrationStatus.UPDATED, desired)


def merge_json_mcp(
    content: str | None,
    server: Mapping[str, Any],
    *,
    action: str = "install",
    owned: bool = False,
) -> PlannedFileChange:
    """Strictly merge only ``mcpServers.tg-recall``; reject duplicate JSON keys."""

    try:
        document = _strict_json_loads(content or "{}")
    except (json.JSONDecodeError, IntegrationConflictError) as exc:
        return PlannedFileChange(IntegrationStatus.CONFLICT, conflict=f"invalid JSON configuration: {exc}")
    if not isinstance(document, dict):
        return PlannedFileChange(IntegrationStatus.CONFLICT, conflict="JSON configuration root must be an object")
    mcp_servers = document.get("mcpServers")
    if mcp_servers is None:
        mcp_servers = {}
        document["mcpServers"] = mcp_servers
    if not isinstance(mcp_servers, dict):
        return PlannedFileChange(IntegrationStatus.CONFLICT, conflict="mcpServers must be an object")
    wanted = _canonical_json_value(server)
    current = mcp_servers.get(OWNER)
    if action == "uninstall":
        if current is None:
            return PlannedFileChange(IntegrationStatus.UNCHANGED, _render_json(document))
        if not owned:
            return PlannedFileChange(IntegrationStatus.CONFLICT, conflict="unowned tg-recall MCP entry")
        del mcp_servers[OWNER]
        if not mcp_servers:
            del document["mcpServers"]
        return PlannedFileChange(IntegrationStatus.UPDATED, _render_json(document))
    if current is None:
        mcp_servers[OWNER] = dict(server)
        return PlannedFileChange(IntegrationStatus.INSTALLED, _render_json(document))
    if _canonical_json_value(current) == wanted:
        return PlannedFileChange(IntegrationStatus.UNCHANGED, _render_json(document))
    if action == "refresh" and owned:
        mcp_servers[OWNER] = dict(server)
        return PlannedFileChange(IntegrationStatus.UPDATED, _render_json(document))
    return PlannedFileChange(
        IntegrationStatus.CONFLICT, conflict="unowned tg-recall MCP entry conflicts with requested configuration"
    )


def render_owned_toml_block(body: str, *, guide_version: str) -> str:
    normalized = _normalize_body(body)
    digest = _sha256(normalized.encode("utf-8"))
    return (
        f"# tg-recall:begin owner={OWNER} schema={INTEGRATION_SCHEMA_VERSION} guide={guide_version} sha256={digest}\n"
        f"{normalized}\n{_TOML_MARKER_END}\n"
    )


def apply_owned_toml_block(
    content: str | None, body: str, *, guide_version: str, action: str = "install"
) -> PlannedFileChange:
    """Plan a delimited TOML MCP table without reserializing unrelated TOML."""

    existing = content or ""
    try:
        _validate_toml(existing)
        span = _find_owned_block(existing, _TOML_BEGIN_RE, _TOML_MARKER_START, _TOML_MARKER_END)
    except IntegrationConflictError as exc:
        return PlannedFileChange(IntegrationStatus.CONFLICT, conflict=str(exc))
    if span is None and _UNOWNED_TOML_SERVER_RE.search(existing):
        return PlannedFileChange(IntegrationStatus.CONFLICT, conflict="unowned tg-recall TOML table")
    desired = render_owned_toml_block(body, guide_version=guide_version)
    if span is None:
        if action == "uninstall":
            return PlannedFileChange(IntegrationStatus.UNCHANGED, existing)
        candidate = existing + ("" if not existing or existing.endswith("\n") else "\n") + desired
        try:
            _validate_toml(candidate)
        except IntegrationConflictError as exc:
            return PlannedFileChange(IntegrationStatus.CONFLICT, conflict=str(exc))
        return PlannedFileChange(IntegrationStatus.INSTALLED, candidate)
    start, end, current_body, marker = span
    if _sha256(current_body.encode("utf-8")) != marker["digest"]:
        return PlannedFileChange(IntegrationStatus.CONFLICT, conflict="owned TOML block was edited")
    candidate = existing[:start] + ("" if action == "uninstall" else desired) + existing[end:]
    try:
        _validate_toml(candidate)
    except IntegrationConflictError as exc:
        return PlannedFileChange(IntegrationStatus.CONFLICT, conflict=str(exc))
    if action == "uninstall":
        return PlannedFileChange(IntegrationStatus.UPDATED, candidate or None)
    if existing[start:end] == desired:
        return PlannedFileChange(IntegrationStatus.UNCHANGED, existing)
    return PlannedFileChange(IntegrationStatus.UPDATED, candidate)


def load_ownership_state(state_root: str | Path) -> tuple[OwnershipRecord, ...]:
    root, _ = _validated_root(state_root)
    path = _ownership_path(root)
    if not path.exists():
        return ()
    _reject_reparse(path)
    try:
        raw = _strict_json_loads(path.read_text(encoding="utf-8"))
        records = raw["records"]
        if not isinstance(records, list):
            raise TypeError("records must be an array")
        return tuple(
            OwnershipRecord(
                harness=item["harness"],
                scope=IntegrationScope(item["scope"]),
                component=IntegrationComponent(item["component"]),
                target=item["target"],
                guide_version=item["guide_version"],
                digest=item["digest"],
                updated_at=item["updated_at"],
                backup_path=item.get("backup_path"),
            )
            for item in records
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, IntegrationConflictError) as exc:
        raise IntegrationConflictError(f"invalid integration ownership state: {exc}") from exc


def save_ownership_state(state_root: str | Path, records: Sequence[OwnershipRecord]) -> Path:
    root = _absolute_path(state_root)
    if not root.exists() or not root.is_dir():
        raise IntegrationPathError("ownership state root must be an existing directory")
    _reject_reparse_path(root)
    state_path = _ownership_path(root)
    payload = {
        "schema_version": INTEGRATION_SCHEMA_VERSION,
        "records": [
            item.as_json()
            for item in sorted(
                records, key=lambda item: (item.harness, item.scope.value, item.component.value, item.target)
            )
        ],
    }
    _atomic_replace(
        state_path,
        (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        root=root,
    )
    return state_path


def ownership_record(
    *,
    harness: str,
    scope: IntegrationScope,
    component: IntegrationComponent,
    target: str | Path,
    guide_version: str,
    content: str,
    backup: str | Path | None = None,
) -> OwnershipRecord:
    return OwnershipRecord(
        harness=harness,
        scope=scope,
        component=component,
        target=str(Path(target)),
        guide_version=guide_version,
        digest=_sha256(content.encode("utf-8")),
        updated_at=datetime.now(UTC).isoformat(),
        backup_path=str(backup) if backup else None,
    )


def _atomic_replace(target: Path, payload: bytes, *, root: str | Path, create_backup: bool = True) -> FileMutation:
    target = validate_integration_target(target, root=root)
    if target.exists() and target.read_bytes() == payload:
        return FileMutation(target=target, backup=None, changed=False)
    backup = _write_backup(target) if create_backup and target.exists() else None
    temporary: Path | None = None
    try:
        fd, raw_temporary = tempfile.mkstemp(prefix=f".{target.name}.tg-recall-", suffix=".tmp", dir=target.parent)
        temporary = Path(raw_temporary)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            _make_private(temporary, is_dir=False)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        # Revalidate after all work that can be interrupted before replacement.
        validate_integration_target(target, root=root)
        os.replace(temporary, target)
        _make_private(target, is_dir=False)
        return FileMutation(target=target, backup=backup, changed=True)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _remove_owned_file(target: Path, *, root: str | Path) -> FileMutation:
    target = validate_integration_target(target, root=root)
    if not target.exists():
        return FileMutation(target=target, backup=None, changed=False)
    backup = _write_backup(target)
    validate_integration_target(target, root=root)
    target.unlink()
    return FileMutation(target=target, backup=backup, changed=True)


def _write_backup(target: Path) -> Path:
    if not target.exists():
        raise IntegrationPathError("cannot backup a missing integration target")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    digest = _sha256(target.read_bytes())[:12]
    backup = target.with_name(f".{target.name}.tg-recall-backup-{stamp}-{digest}.bak")
    try:
        with backup.open("xb") as handle:
            handle.write(target.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:  # practically impossible, but never overwrite a recovery file
        raise IntegrationPathError("backup name collision") from None
    try:
        _make_private(backup, is_dir=False)
    except Exception:
        backup.unlink(missing_ok=True)
        raise
    return backup


def _find_owned_block(
    content: str,
    begin_re: re.Pattern[str],
    begin_token: str,
    end_token: str,
) -> tuple[int, int, str, Mapping[str, str]] | None:
    raw_begin_count = content.count(begin_token)
    raw_end_count = content.count(end_token)
    matches = list(begin_re.finditer(content))
    if raw_begin_count != len(matches) or raw_begin_count != raw_end_count:
        raise IntegrationConflictError("malformed, duplicate, or unclosed ownership marker")
    if not matches:
        return None
    if len(matches) != 1:
        raise IntegrationConflictError("duplicate ownership marker")
    match = matches[0]
    end_start = content.find(end_token, match.end())
    if end_start < 0:
        raise IntegrationConflictError("unclosed ownership marker")
    body_start = match.end()
    if content[body_start : body_start + 1] == "\n":
        body_start += 1
    body_end = end_start
    if body_end > body_start and content[body_end - 1 : body_end] == "\n":
        body_end -= 1
    end = end_start + len(end_token)
    if content[end : end + 1] == "\n":
        end += 1
    return match.start(), end, content[body_start:body_end], match.groupdict()


def _strict_json_loads(content: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise IntegrationConflictError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(content, object_pairs_hook=reject_duplicates)


def _render_json(document: Mapping[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"


def _canonical_json_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _validate_toml(content: str) -> None:
    if not content:
        return
    try:
        assert tomllib is not None
        tomllib.loads(content)
    except Exception as exc:
        raise IntegrationConflictError(f"invalid TOML configuration: {exc}") from exc


def _ownership_path(root: str | Path) -> Path:
    return _absolute_path(root) / "integration-ownership.json"


def _validated_root(root: str | Path) -> tuple[Path, Path]:
    root_path = _absolute_path(root)
    if not root_path.exists() or not root_path.is_dir():
        raise IntegrationPathError("integration root must be an existing directory")
    _reject_reparse_path(root_path)
    return root_path, root_path.resolve(strict=True)


def _absolute_path(value: str | Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(value))))


def _reject_reparse_path(path: Path) -> None:
    current = path.anchor and Path(path.anchor) or Path()
    for part in path.parts[1:] if path.anchor else path.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _reject_reparse(current)


def _reject_reparse(path: Path) -> None:
    try:
        data = path.lstat()
    except FileNotFoundError:
        return
    is_reparse = bool(getattr(data, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    if path.is_symlink() or is_reparse:
        raise IntegrationPathError(f"symlink or reparse point is not allowed: {path}")


def _make_private(path: Path, *, is_dir: bool) -> None:
    if os.name == "nt":
        finding = harden_path(path, is_dir=is_dir)
        if finding.status != "ok":
            raise IntegrationPathError(f"could not restrict private integration file ACL: {finding.detail}")
        return
    path.chmod(0o700 if is_dir else 0o600)


def _normalize_body(body: str) -> str:
    if not isinstance(body, str):
        raise TypeError("owned block body must be text")
    return body.rstrip("\n")


def _normalize_yaml_frontmatter(frontmatter: str) -> str:
    if not isinstance(frontmatter, str):
        raise TypeError("YAML frontmatter must be text")
    lines = frontmatter.rstrip("\n").splitlines()
    if len(lines) < 2 or lines[0] != "---" or lines[-1] != "---":
        raise IntegrationConflictError("dedicated YAML front matter must start and end with ---")
    return "\n".join(lines) + "\n"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
