from __future__ import annotations

import pytest

from tg_recall.security import CONFIRMATION_PHRASE, enforce_ai_archive_read, require_human_confirmation


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
