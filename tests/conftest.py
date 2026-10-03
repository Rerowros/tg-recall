from __future__ import annotations

import os

import pytest

from tg_recall.security import AGENT_ENV, AGENT_ENV_PREFIXES, AUTOMATION_ENV


@pytest.fixture(autouse=True)
def _human_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests start as a human operator even when pytest itself runs under CI or a coding agent."""

    for name in (*AUTOMATION_ENV, *AGENT_ENV, "TG_RECALL_TRUSTED_AUTOMATION", "TG_ECOSYSTEM_TRUSTED_AUTOMATION"):
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith(AGENT_ENV_PREFIXES):
            monkeypatch.delenv(name, raising=False)
