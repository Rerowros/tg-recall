from __future__ import annotations

import getpass
import os
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping


CONFIRMATION_PHRASE = "I understand this can change my local Telegram archive"
TRUSTED_AUTOMATION_ENV = "TG_RECALL_TRUSTED_AUTOMATION"


class AgentOperation(StrEnum):
    """Operations that can be reached from an automated interface.

    Keep this table at the boundary instead of relying on individual command
    handlers.  New CLI or MCP entrypoints must opt in explicitly.
    """

    AGENT_GUIDE = "agent_guide"
    ARCHIVE_READ = "archive_read"
    ARCHIVE_EXPORT = "archive_export"
    METADATA_LIST = "metadata_list"
    SYNC = "sync"
    MEDIA_MATERIALIZE = "media_materialize"
    TRANSCRIBE = "transcribe"
    HUMAN_ONLY = "human_only"


@dataclass(frozen=True)
class OperationCapability:
    agent_allowed: bool
    scope_required: bool = False
    state_changing: bool = False


OPERATION_CAPABILITIES: dict[AgentOperation, OperationCapability] = {
    AgentOperation.AGENT_GUIDE: OperationCapability(agent_allowed=True),
    AgentOperation.ARCHIVE_READ: OperationCapability(agent_allowed=True, scope_required=True),
    AgentOperation.ARCHIVE_EXPORT: OperationCapability(agent_allowed=True, scope_required=True, state_changing=True),
    AgentOperation.METADATA_LIST: OperationCapability(agent_allowed=True),
    AgentOperation.SYNC: OperationCapability(agent_allowed=True, scope_required=True, state_changing=True),
    AgentOperation.MEDIA_MATERIALIZE: OperationCapability(agent_allowed=True, scope_required=True, state_changing=True),
    AgentOperation.TRANSCRIBE: OperationCapability(agent_allowed=True, scope_required=True, state_changing=True),
    AgentOperation.HUMAN_ONLY: OperationCapability(agent_allowed=False, state_changing=True),
}


@dataclass(frozen=True)
class RequestedAgentScope:
    """Requested scope before it is intersected with the configured policy."""

    chat_ids: tuple[int, ...] = ()
    since: str | None = None
    until: str | None = None
    media_policy: str | None = None
    result_limit: int | None = None
    saved_scope: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class PolicyDecision:
    operation: AgentOperation
    allowed: bool
    chat_ids: tuple[int, ...] = ()
    since: str | None = None
    until: str | None = None
    media_policy: str | None = None
    result_limit: int | None = None
    error_code: str | None = None
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class AgentPolicyError(PermissionError):
    """A safe, stable error for an agent policy decision."""

    def __init__(self, decision: PolicyDecision):
        self.decision = decision
        self.error_code = decision.error_code or "agent_policy_denied"
        self.details = decision.details
        super().__init__(decision.message or "operation is denied by agent policy")


def resolve_agent_policy(
    operation: AgentOperation,
    *,
    enabled: bool,
    allowed_chat_ids: list[int],
    max_results: int,
    requested: RequestedAgentScope = RequestedAgentScope(),
    allowed_since: str | None = None,
    allowed_until: str | None = None,
    allowed_media_types: str = "all",
    automation: bool | None = None,
) -> PolicyDecision:
    """Return the effective automation scope without exposing hidden policy data.

    Interactive users retain the existing CLI behaviour.  MCP passes
    ``automation=True`` because every MCP caller is an automated client.
    """

    if automation is None:
        automation = is_automation_shell()
    capability = OPERATION_CAPABILITIES[operation]
    if not automation:
        return PolicyDecision(
            operation=operation,
            allowed=True,
            chat_ids=requested.chat_ids,
            since=requested.since,
            until=requested.until,
            media_policy=requested.media_policy,
            result_limit=requested.result_limit,
        )
    if not capability.agent_allowed:
        return _denied(operation, "agent_operation_forbidden", "this operation is not available to automation")
    if operation == AgentOperation.AGENT_GUIDE:
        return PolicyDecision(operation=operation, allowed=True)
    if not enabled:
        return _denied(operation, "ai_access_disabled", "archive access is disabled for AI/automation mode")
    if not allowed_chat_ids:
        return _denied(operation, "ai_access_unconfigured", "AI/automation archive access requires allowed chat ids")
    if not isinstance(max_results, int) or isinstance(max_results, bool) or max_results < 1:
        return _denied(operation, "invalid_ai_access_limit", "ai_access.max_results must be a positive integer")

    saved = requested.saved_scope or {}
    saved_chats = tuple(int(value) for value in saved.get("chat_ids", ()))
    requested_chats = requested.chat_ids
    if requested_chats and saved_chats:
        requested_chats = tuple(chat_id for chat_id in requested_chats if chat_id in set(saved_chats))
    elif not requested_chats:
        requested_chats = saved_chats
    if capability.scope_required and not requested_chats:
        code = "scope_empty" if requested.chat_ids and saved_chats else "explicit_scope_required"
        return _denied(operation, code, "AI/automation archive access requires an explicit permitted chat scope")
    effective_chats = tuple(chat_id for chat_id in requested_chats if chat_id in set(allowed_chat_ids))
    if capability.scope_required and not effective_chats:
        return _denied(operation, "chat_not_allowed", "requested chat scope is not allowed for AI/automation access")

    _validate_policy_dates(operation, requested.since, requested.until, saved.get("since"), saved.get("until"), allowed_since, allowed_until)
    requested_since = _later_date(requested.since, saved.get("since"), operation)
    requested_until = _earlier_date(requested.until, saved.get("until"), operation)
    since = _later_date(requested_since, allowed_since, operation)
    until = _earlier_date(requested_until, allowed_until, operation)
    if since and until and _parse_policy_date(since, operation) > _parse_policy_date(until, operation):
        return _denied(operation, "scope_empty", "requested date range has no overlap with AI/automation policy")

    saved_media = saved.get("media_policy")
    requested_media = requested.media_policy
    scoped_media = _intersect_media_policies(requested_media, saved_media) if requested_media is not None and saved_media is not None else (requested_media or saved_media)
    media_policy = _intersect_media_policies(scoped_media, allowed_media_types)
    if scoped_media is not None and media_policy == "none" and _media_values(scoped_media):
        return _denied(operation, "media_not_allowed", "requested media policy has no overlap with AI/automation policy")

    limit = requested.result_limit
    if limit is not None:
        if limit < 1:
            return _denied(operation, "invalid_result_limit", "result limit must be positive")
        limit = min(limit, max_results)
    elif operation in {AgentOperation.ARCHIVE_READ, AgentOperation.ARCHIVE_EXPORT, AgentOperation.METADATA_LIST}:
        limit = max_results
    return PolicyDecision(
        operation=operation,
        allowed=True,
        chat_ids=effective_chats,
        since=since,
        until=until,
        media_policy=media_policy,
        result_limit=limit,
        details={"scope_narrowed": effective_chats != requested_chats or since != requested.since or until != requested.until},
    )


def require_agent_policy(*args: Any, **kwargs: Any) -> PolicyDecision:
    decision = resolve_agent_policy(*args, **kwargs)
    if not decision.allowed:
        raise AgentPolicyError(decision)
    return decision


def audit_policy_decision(db: Any, decision: PolicyDecision, *, requested: RequestedAgentScope) -> None:
    """Store useful policy evidence while never recording raw query/message text."""

    db.audit(
        "agent_policy_decision",
        decision.operation.value,
        allowed=decision.allowed,
        error_code=decision.error_code,
        requested_chat_ids=list(requested.chat_ids),
        effective_chat_ids=list(decision.chat_ids),
        since=decision.since,
        until=decision.until,
        media_policy=decision.media_policy,
        result_limit=decision.result_limit,
    )


def _denied(operation: AgentOperation, code: str, message: str) -> PolicyDecision:
    return PolicyDecision(operation=operation, allowed=False, error_code=code, message=message, details={"operation": operation.value})


def _validate_policy_dates(operation: AgentOperation, *values: str | None) -> None:
    for value in values:
        if value is not None:
            _parse_policy_date(value, operation)


def _parse_policy_date(value: str, operation: AgentOperation) -> date:
    try:
        if not isinstance(value, str):
            raise TypeError("scope date is not a string")
        if len(value) == 10:
            return date.fromisoformat(value)
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except (TypeError, ValueError) as exc:
        raise AgentPolicyError(_denied(operation, "invalid_scope_date", "scope dates must be strict ISO-8601 values")) from exc


def _later_date(first: str | None, second: str | None, operation: AgentOperation) -> str | None:
    if not first:
        return second
    if not second:
        return first
    return first if _parse_policy_date(first, operation) >= _parse_policy_date(second, operation) else second


def _earlier_date(first: str | None, second: str | None, operation: AgentOperation) -> str | None:
    if not first:
        return second
    if not second:
        return first
    return first if _parse_policy_date(first, operation) <= _parse_policy_date(second, operation) else second


def _media_values(value: str | None) -> set[str]:
    if value is None or value == "all":
        return {"all"}
    return {item.strip() for item in value.split(",") if item.strip() and item.strip() != "none"}


def _intersect_media_policies(requested: str | None, allowed: str | None) -> str | None:
    if requested is None:
        return allowed
    request_values = _media_values(requested)
    allowed_values = _media_values(allowed or "all")
    if "all" in allowed_values:
        result = request_values
    elif "all" in request_values:
        result = allowed_values
    else:
        result = request_values & allowed_values
    return ",".join(sorted(result)) if result else "none"


@dataclass(frozen=True)
class SecurityFinding:
    path: str
    status: str
    detail: str


def is_automation_shell() -> bool:
    return any(
        os.environ.get(name, "").lower() in {"1", "true", "yes"}
        for name in ["CI", "CODEX", "AI_TERMINAL", "TG_RECALL_AI_MODE", "TG_ECOSYSTEM_AI_MODE"]
    )


def require_human_confirmation(
    operation: str,
    provided_phrase: str | None = None,
    *,
    require_phrase_interactive: bool = False,
) -> None:
    if not is_automation_shell() and sys.stdin.isatty():
        if not require_phrase_interactive:
            return
        typed = input(
            f"{operation} can change local Telegram archive/account data.\n"
            f"Type exactly: {CONFIRMATION_PHRASE}\n> "
        )
        if typed == CONFIRMATION_PHRASE:
            return
        raise PermissionError("confirmation phrase did not match")

    if (
        os.environ.get(TRUSTED_AUTOMATION_ENV) == "1"
        or os.environ.get("TG_ECOSYSTEM_TRUSTED_AUTOMATION") == "1"
    ) and provided_phrase == CONFIRMATION_PHRASE:
        return

    raise PermissionError(
        f"{operation} is blocked in automation mode. Run it from an interactive terminal, "
        f"or set {TRUSTED_AUTOMATION_ENV}=1 and pass --confirm-risk with the exact confirmation phrase."
    )


def enforce_ai_archive_read(
    *,
    enabled: bool,
    allowed_chat_ids: list[int],
    requested_chat_id: int | None,
    requested_limit: int,
    max_results: int,
) -> int:
    """Backward-compatible helper for integrations written before policy routing."""

    decision = require_agent_policy(
        AgentOperation.ARCHIVE_READ,
        enabled=enabled,
        allowed_chat_ids=allowed_chat_ids,
        max_results=max_results,
        requested=RequestedAgentScope(
            chat_ids=(requested_chat_id,) if requested_chat_id is not None else (),
            result_limit=requested_limit,
        ),
    )
    return decision.result_limit or requested_limit


def harden_path(path: str | Path, *, is_dir: bool | None = None) -> SecurityFinding:
    target = Path(path)
    if not target.exists():
        return SecurityFinding(str(target), "missing", "path does not exist")

    directory = target.is_dir() if is_dir is None else is_dir
    if os.name == "nt":
        return _harden_windows_path(target, directory)

    mode = 0o700 if directory else 0o600
    target.chmod(mode)
    return SecurityFinding(str(target), "ok", f"set POSIX mode {oct(mode)}")


def check_path_private(path: str | Path) -> SecurityFinding:
    target = Path(path)
    if not target.exists():
        return SecurityFinding(str(target), "missing", "path does not exist")
    if os.name == "nt":
        return SecurityFinding(str(target), "unknown", "Windows ACL privacy cannot be fully verified here; run security check --fix")

    mode = stat.S_IMODE(target.stat().st_mode)
    if mode & 0o077:
        return SecurityFinding(str(target), "warning", f"group/other permissions are set: {oct(mode)}")
    return SecurityFinding(str(target), "ok", f"private POSIX mode: {oct(mode)}")


def _harden_windows_path(target: Path, directory: bool) -> SecurityFinding:
    user = getpass.getuser()
    grant = f"{user}:(OI)(CI)F" if directory else f"{user}:F"
    commands = [
        ["icacls", str(target), "/inheritance:r"],
        ["icacls", str(target), "/grant:r", grant],
        ["icacls", str(target), "/remove:g", "*S-1-1-0", "*S-1-5-11", "*S-1-5-32-545"],
    ]
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            return SecurityFinding(str(target), "warning", f"icacls failed: {detail}")
    return SecurityFinding(str(target), "ok", "restricted Windows ACL inheritance and broad groups")
