from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tg_recall.config import AppConfig, TelegramConfig
from tg_recall.storage import Database
from tg_recall.telegram_client import TelegramArchiveClient, TelegramRetryPendingError


class FakeFloodWaitError(Exception):
    def __init__(self, seconds: int):
        super().__init__(f"wait {seconds}")
        self.seconds = seconds


class FakeClient:
    def __init__(self, *, flood: bool = False):
        self.flood = flood
        self.message_args = {}

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

    async def iter_messages(self, chat_id, limit=100, **kwargs):
        self.message_args = {"chat_id": chat_id, "limit": limit, **kwargs}
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
    import tg_recall.telegram_client as module

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


def test_sync_uses_newest_watermark_and_backfill_uses_oldest(tmp_path, monkeypatch) -> None:
    import asyncio
    import tg_recall.telegram_client as module

    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    db.create_scope("work", [10], None, None)
    db.update_sync_state(10, newest_message_id=50, oldest_message_id=10)
    client = TelegramArchiveClient(cfg(), db)
    fake = FakeClient()
    monkeypatch.setattr(client, "_client", lambda: fake)
    monkeypatch.setattr(module, "_load_telethon", lambda: (object, FakeFloodWaitError))

    asyncio.run(client.sync_scope("work"))
    assert fake.message_args["min_id"] == 50

    asyncio.run(client.sync_scope("work", backfill=True))
    assert fake.message_args["max_id"] == 1


def test_sync_rejects_unbounded_limit_and_does_not_call_telegram_before_persisted_retry(tmp_path, monkeypatch) -> None:
    import asyncio

    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    db.create_scope("work", [10], None, None)
    db.update_sync_state(10, retry_after="2999-01-01T00:00:00+00:00")
    client = TelegramArchiveClient(cfg(), db)

    class NeverCalled:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def iter_messages(self, *_args, **_kwargs):
            raise AssertionError("active retry must prevent a Telegram request")
            yield None

    monkeypatch.setattr(client, "_client", lambda: NeverCalled())
    with pytest.raises(TelegramRetryPendingError, match="deferred"):
        asyncio.run(client.sync_scope("work"))
    with pytest.raises(ValueError, match="1..1000"):
        asyncio.run(client.sync_scope("work", limit=0))


def test_incremental_forward_backfill_edits_and_flood_resume_are_gap_safe(tmp_path, monkeypatch) -> None:
    import asyncio
    import tg_recall.telegram_client as module

    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    db.create_scope("work", [10], "2026-01-02", "2026-01-04", "none")
    db.update_sync_state(10, newest_message_id=5, oldest_message_id=5)
    client = TelegramArchiveClient(cfg(), db)
    calls: list[dict[str, int]] = []

    class HistoryClient:
        phase = "forward"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def iter_messages(self, _chat_id, limit=100, **kwargs):
            calls.append({"limit": limit, **kwargs})
            if self.phase == "forward":
                yield _message(8, "newest accepted", datetime(2026, 1, 4, tzinfo=UTC))
                yield _message(7, "edited original", datetime(2026, 1, 3, tzinfo=UTC))
                yield _message(6, "too old", datetime(2026, 1, 1, tzinfo=UTC))
            elif self.phase == "backfill":
                yield _message(4, "historical accepted", datetime(2026, 1, 2, tzinfo=UTC))
                yield _message(3, "before scope", datetime(2026, 1, 1, tzinfo=UTC))
            elif self.phase == "flood":
                yield _message(9, "partial newest", datetime(2026, 1, 4, tzinfo=UTC))
                raise FakeFloodWaitError(3)
            else:
                yield _message(9, "partial newest", datetime(2026, 1, 4, tzinfo=UTC))
                yield _message(8, "newest accepted edited", datetime(2026, 1, 4, tzinfo=UTC))

    fake = HistoryClient()
    monkeypatch.setattr(client, "_client", lambda: fake)
    monkeypatch.setattr(module, "_load_telethon", lambda: (object, FakeFloodWaitError))

    forward = asyncio.run(client.sync_scope("work", limit=10))
    assert forward == {"messages": 2, "media_jobs": 0, "backfill": 0}
    assert calls[-1]["min_id"] == 5
    assert db.get_sync_state(10)["newest_message_id"] == 8
    assert db.get_sync_state(10)["oldest_message_id"] == 5

    fake.phase = "backfill"
    backfill = asyncio.run(client.sync_scope("work", limit=10, backfill=True))
    assert backfill == {"messages": 1, "media_jobs": 0, "backfill": 1}
    assert calls[-1]["max_id"] == 5
    assert db.get_sync_state(10)["newest_message_id"] == 8
    assert db.get_sync_state(10)["oldest_message_id"] == 4

    fake.phase = "flood"
    with pytest.raises(FakeFloodWaitError):
        asyncio.run(client.sync_scope("work", limit=10))
    # The partial newest-first page is stored idempotently, but high stays at
    # the last fully completed forward page so retry cannot skip unseen IDs.
    assert db.get_sync_state(10)["newest_message_id"] == 8
    assert db.get_sync_state(10)["oldest_message_id"] == 4
    db.update_sync_state(10, retry_after=None)

    fake.phase = "resume"
    resumed = asyncio.run(client.sync_scope("work", limit=10))
    assert resumed == {"messages": 2, "media_jobs": 0, "backfill": 0}
    assert calls[-1]["min_id"] == 8
    state = db.get_sync_state(10)
    assert state["newest_message_id"] == 9
    assert state["oldest_message_id"] == 4
    with db.connect() as conn:
        rows = conn.execute("SELECT message_id, text FROM messages WHERE chat_id = 10 ORDER BY message_id").fetchall()
        media_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE stage = 'media_download'").fetchone()[0]
    assert [tuple(row) for row in rows] == [(4, "historical accepted"), (7, "edited original"), (8, "newest accepted edited"), (9, "partial newest")]
    assert media_jobs == 0


def test_backfill_flood_checkpoints_low_watermark_and_resumes_strictly_older(tmp_path, monkeypatch) -> None:
    import asyncio
    import tg_recall.telegram_client as module

    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    db.create_scope("work", [10], None, None)
    db.update_sync_state(10, newest_message_id=50, oldest_message_id=10)
    client = TelegramArchiveClient(cfg(), db)
    arguments: list[dict[str, int]] = []

    class BackfillClient:
        attempt = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def iter_messages(self, _chat_id, limit=100, **kwargs):
            arguments.append({"limit": limit, **kwargs})
            if self.attempt == 0:
                yield _message(9, "older page", datetime(2026, 1, 3, tzinfo=UTC))
                raise FakeFloodWaitError(3)
            yield _message(8, "older resume", datetime(2026, 1, 2, tzinfo=UTC))

    fake = BackfillClient()
    monkeypatch.setattr(client, "_client", lambda: fake)
    monkeypatch.setattr(module, "_load_telethon", lambda: (object, FakeFloodWaitError))
    with pytest.raises(FakeFloodWaitError):
        asyncio.run(client.sync_scope("work", backfill=True))
    assert db.get_sync_state(10)["newest_message_id"] == 50
    assert db.get_sync_state(10)["oldest_message_id"] == 9

    db.update_sync_state(10, retry_after=None)
    fake.attempt = 1
    asyncio.run(client.sync_scope("work", backfill=True))
    assert arguments[0]["max_id"] == 10
    assert arguments[1]["max_id"] == 9
    assert db.get_sync_state(10)["newest_message_id"] == 50
    assert db.get_sync_state(10)["oldest_message_id"] == 8


def test_partial_non_flood_failure_replays_idempotently_before_forward_high_advances(tmp_path, monkeypatch) -> None:
    import asyncio
    import tg_recall.telegram_client as module

    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    db.create_scope("work", [10], None, None)
    db.update_sync_state(10, newest_message_id=5, oldest_message_id=5)
    client = TelegramArchiveClient(cfg(), db)

    class InterruptedForward:
        attempt = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def iter_messages(self, _chat_id, limit=100, **_kwargs):
            yield _message(8, "first stored before interruption", datetime(2026, 1, 3, tzinfo=UTC))
            if self.attempt == 0:
                raise RuntimeError("connection interrupted")
            yield _message(7, "remaining after resume", datetime(2026, 1, 2, tzinfo=UTC))

    fake = InterruptedForward()
    monkeypatch.setattr(client, "_client", lambda: fake)
    monkeypatch.setattr(module, "_load_telethon", lambda: (object, FakeFloodWaitError))
    with pytest.raises(RuntimeError, match="interrupted"):
        asyncio.run(client.sync_scope("work"))
    assert db.get_sync_state(10)["newest_message_id"] == 5

    fake.attempt = 1
    asyncio.run(client.sync_scope("work"))
    assert db.get_sync_state(10)["newest_message_id"] == 8
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE chat_id = 10").fetchone()[0] == 2


def _message(message_id: int, text: str, value: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        date=value,
        raw_text=text,
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
