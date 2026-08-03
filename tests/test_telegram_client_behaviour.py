from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tg_ecosystem.config import AppConfig, TelegramConfig
from tg_ecosystem.storage import Database
from tg_ecosystem.telegram_client import TelegramArchiveClient


class FakeFloodWaitError(Exception):
    def __init__(self, seconds: int):
        super().__init__(f"wait {seconds}")
        self.seconds = seconds


class FakeClient:
    def __init__(self, *, flood: bool = False):
        self.flood = flood

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def iter_dialogs(self, limit=None):
        yield SimpleNamespace(
            id=10,
            name="Work",
            is_user=False,
            is_group=True,
            is_channel=False,
            entity=SimpleNamespace(username="work"),
        )

    async def iter_messages(self, chat_id, limit=100):
        if self.flood:
            raise FakeFloodWaitError(3)
        yield SimpleNamespace(
            id=1,
            date=datetime(2026, 1, 1, tzinfo=UTC),
            raw_text="deadline",
            text=None,
            sender_id=7,
            sender=None,
            reply_to=None,
            fwd_from=None,
            edit_date=None,
            voice=None,
            audio=None,
            video=None,
            photo=None,
            document=None,
            media=None,
        )


def cfg() -> AppConfig:
    return AppConfig(telegram=TelegramConfig(api_id=1, api_hash="hash"))


def test_chat_discovery_with_mocked_telegram(tmp_path, monkeypatch) -> None:
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    client = TelegramArchiveClient(cfg(), db)
    monkeypatch.setattr(client, "_client", lambda: FakeClient())

    # Pytest has no built-in async runner; use asyncio explicitly.
    import asyncio

    chats = asyncio.run(client.discover_chats())
    assert chats[0].chat_id == 10
    assert db.list_chats()[0]["title"] == "Work"


def test_flood_wait_is_persisted(tmp_path, monkeypatch) -> None:
    import asyncio
    import tg_ecosystem.telegram_client as module

    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    db.create_scope("work", [10], None, None)
    client = TelegramArchiveClient(cfg(), db)
    monkeypatch.setattr(client, "_client", lambda: FakeClient(flood=True))
    monkeypatch.setattr(module, "_load_telethon", lambda: (object, FakeFloodWaitError))

    with pytest.raises(FakeFloodWaitError):
        asyncio.run(client.sync_scope("work"))

    with db.connect() as conn:
        row = conn.execute("SELECT retry_after FROM sync_state WHERE chat_id = 10").fetchone()
    assert row["retry_after"] is not None
