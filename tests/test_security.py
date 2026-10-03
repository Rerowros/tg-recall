from __future__ import annotations

import pytest

from tg_recall.security import CONFIRMATION_PHRASE, enforce_ai_archive_read, is_automation_shell, require_human_confirmation


def test_ci_marker_is_automation_shell(monkeypatch) -> None:
    for name in ["CODEX", "AI_TERMINAL", "TG_RECALL_AI_MODE", "TG_ECOSYSTEM_AI_MODE"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CI", "true")

    assert is_automation_shell() is True


def test_ai_archive_read_disabled_by_default(monkeypatch) -> None:
    monkeypatch.setenv("TG_RECALL_AI_MODE", "1")

    with pytest.raises(PermissionError, match="disabled"):
        enforce_ai_archive_read(
            enabled=False,
            allowed_chat_ids=[],
            requested_chat_id=10,
            requested_limit=20,
            max_results=5,
        )


def test_ai_archive_read_requires_allowed_chat(monkeypatch) -> None:
    monkeypatch.setenv("TG_RECALL_AI_MODE", "1")

    with pytest.raises(PermissionError, match="not allowed"):
        enforce_ai_archive_read(
            enabled=True,
            allowed_chat_ids=[10],
            requested_chat_id=11,
            requested_limit=20,
            max_results=5,
        )


def test_ai_archive_read_caps_limit(monkeypatch) -> None:
    monkeypatch.setenv("TG_RECALL_AI_MODE", "1")

    limit = enforce_ai_archive_read(
        enabled=True,
        allowed_chat_ids=[10],
        requested_chat_id=10,
        requested_limit=20,
        max_results=5,
    )

    assert limit == 5


def test_dangerous_operation_blocked_in_automation(monkeypatch) -> None:
    monkeypatch.setenv("TG_RECALL_AI_MODE", "1")
    monkeypatch.delenv("TG_RECALL_TRUSTED_AUTOMATION", raising=False)

    with pytest.raises(PermissionError, match="blocked"):
        require_human_confirmation("purge", CONFIRMATION_PHRASE)


def test_trusted_automation_requires_exact_confirmation(monkeypatch) -> None:
    monkeypatch.setenv("TG_RECALL_AI_MODE", "1")
    monkeypatch.setenv("TG_RECALL_TRUSTED_AUTOMATION", "1")

    require_human_confirmation("scripted maintenance", CONFIRMATION_PHRASE)


class _PipedStdin:
    def isatty(self) -> bool:
        return False


class _TerminalStdin:
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(
    "name,value",
    [("CLAUDECODE", "1"), ("AI_AGENT", "claude-code_2-1-286_agent"), ("CODEX_SANDBOX", "seatbelt")],
)
def test_coding_agent_shell_is_automation(monkeypatch, name, value) -> None:
    monkeypatch.setattr("sys.stdin", _PipedStdin())
    monkeypatch.setenv(name, value)

    assert is_automation_shell() is True


def test_owner_terminal_inside_agent_app_stays_human(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", _TerminalStdin())
    monkeypatch.setenv("CLAUDECODE", "1")

    assert is_automation_shell() is False


def test_agent_cannot_use_trusted_automation_escape_hatch(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", _PipedStdin())
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("TG_RECALL_TRUSTED_AUTOMATION", "1")

    with pytest.raises(PermissionError, match="not available to AI agents"):
        require_human_confirmation("telegram auth", CONFIRMATION_PHRASE)
