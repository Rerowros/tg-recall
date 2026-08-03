from __future__ import annotations

import getpass
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


CONFIRMATION_PHRASE = "I understand this can change my local Telegram archive"
TRUSTED_AUTOMATION_ENV = "TG_RECALL_TRUSTED_AUTOMATION"


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
    if not is_automation_shell():
        return requested_limit

    if not enabled:
        raise PermissionError("archive reads are disabled for AI/automation mode")
    if not allowed_chat_ids:
        raise PermissionError("AI/automation archive reads require at least one allowed chat id")
    if requested_chat_id is None:
        raise PermissionError("AI/automation archive reads must include --chat-id")
    if requested_chat_id not in allowed_chat_ids:
        raise PermissionError(f"chat {requested_chat_id} is not allowed for AI/automation archive reads")
    return min(requested_limit, max_results)


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
