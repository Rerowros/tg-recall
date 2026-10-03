from __future__ import annotations

import asyncio
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


class _FakeTelethonBase:
    authorized = False

    def __init__(self, *args, **kwargs):
        self.calls: list[str] = []

    async def connect(self):
        self.calls.append("connect")

    async def disconnect(self):
        self.calls.append("disconnect")

    async def __aexit__(self, exc_type, exc, tb):
        await self.disconnect()

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        return SimpleNamespace(id=1, username="me") if self.authorized else None

    async def start(self, *args, **kwargs):
        raise AssertionError("interactive start() must not run")

    async def iter_dialogs(self, limit=None):
        if False:
            yield None


def _noninteractive_client(monkeypatch, tmp_path, *, authorized: bool):
    import tg_recall.telegram_client as module

    base = type("FakeTelethon", (_FakeTelethonBase,), {"authorized": authorized})
    monkeypatch.setattr(module, "_load_telethon", lambda: (base, FakeFloodWaitError))
    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    return TelegramArchiveClient(cfg(), db)


def test_check_reports_unauthorized_session_without_prompting(tmp_path, monkeypatch) -> None:
    client = _noninteractive_client(monkeypatch, tmp_path, authorized=False)

    assert asyncio.run(client.check()) == {"authorized": False, "user_id": None, "username": None}


def test_archive_operations_fail_fast_without_session(tmp_path, monkeypatch) -> None:
    from tg_recall.telegram_client import TelegramNotAuthorizedError

    client = _noninteractive_client(monkeypatch, tmp_path, authorized=False)

    with pytest.raises(TelegramNotAuthorizedError, match="tg-recall telegram auth"):
        asyncio.run(client.discover_chats())


def test_archive_operations_run_with_saved_session(tmp_path, monkeypatch) -> None:
    client = _noninteractive_client(monkeypatch, tmp_path, authorized=True)

    assert asyncio.run(client.discover_chats()) == []
    assert asyncio.run(client.check())["authorized"] is True


def _msg(message_id, minutes_ago, *, reply_to=None, text="hi"):
    from datetime import timedelta

    return SimpleNamespace(
        id=message_id,
        date=datetime(2026, 10, 1, tzinfo=UTC) - timedelta(minutes=minutes_ago),
        raw_text=text,
        text=None,
        sender_id=7,
        sender=None,
        reply_to=reply_to,
        fwd_from=None,
        edit_date=None,
        voice=None,
        audio=None,
        video=None,
        photo=None,
        document=None,
        media=None,
    )


def _in_topic(topic, reply_to=None):
    return SimpleNamespace(forum_topic=True, reply_to_msg_id=reply_to or topic, reply_to_top_id=topic if reply_to else None)


def test_forum_topic_membership_is_not_a_reply() -> None:
    from tg_recall.telegram_client import forward_label, message_from_telethon

    straight = message_from_telethon(-100, _msg(200, 5, reply_to=_in_topic(157)), forum=True)
    answer = message_from_telethon(-100, _msg(201, 4, reply_to=_in_topic(157, reply_to=200)), forum=True)
    general = message_from_telethon(-100, _msg(202, 3), forum=True)
    plain = message_from_telethon(10, _msg(5, 3, reply_to=SimpleNamespace(reply_to_msg_id=4)))

    assert (straight.topic_id, straight.reply_to_message_id) == (157, None)
    assert (answer.topic_id, answer.reply_to_message_id) == (157, 200)
    assert (general.topic_id, general.reply_to_message_id) == (1, None)
    assert (plain.topic_id, plain.reply_to_message_id) == (None, 4)
    assert forward_label(SimpleNamespace(from_name="News", from_id=None)) == "News"
    assert forward_label(SimpleNamespace(from_name=None, from_id=SimpleNamespace(channel_id=42))) == "channel:42"


class FakeForum:
    """A forum with topic 157 (ids 300..399 every minute) and General noise."""

    def __init__(self):
        self.calls = []
        self.messages = [_msg(i, 1000 - i, reply_to=_in_topic(157)) for i in range(300, 400)]
        self.messages += [_msg(i, 1000 - i) for i in range(400, 450)]

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get_entity(self, chat_id):
        return SimpleNamespace(forum=True)

    async def __call__(self, request):
        return SimpleNamespace(topics=[SimpleNamespace(id=157, title="Русский")])

    async def iter_messages(self, chat_id, limit=None, reply_to=None, wait_time=None, min_id=0, max_id=0, reverse=False):
        self.calls.append({"reply_to": reply_to, "min_id": min_id, "max_id": max_id, "reverse": reverse})
        rows = [m for m in self.messages if reply_to is None or (m.reply_to and m.reply_to.reply_to_msg_id == reply_to)]
        rows = [m for m in rows if m.id > min_id and (not max_id or m.id < max_id)]
        rows.sort(key=lambda m: m.id, reverse=not reverse)
        for message in rows:
            yield message


def test_sync_chat_fetches_one_topic_since_a_date_and_resumes(tmp_path, monkeypatch) -> None:
    from datetime import timedelta

    import tg_recall.telegram_client as module

    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    fake = FakeForum()
    client = TelegramArchiveClient(cfg(), db)
    monkeypatch.setattr(client, "_client", lambda: fake)
    monkeypatch.setattr(module, "_load_telethon", lambda: (object, FakeFloodWaitError))
    since = datetime(2026, 10, 1, tzinfo=UTC) - timedelta(minutes=1000 - 350)

    first = asyncio.run(client.sync_chat(-100, topic_id=157, since=since, max_messages=20))
    assert fake.calls[0]["reply_to"] == 157  # GetReplies for the topic, not the whole group
    assert first["complete"] is False and first["fetched"] == 20
    second = asyncio.run(client.sync_chat(-100, topic_id=157, since=since))
    assert second["complete"] is True
    assert fake.calls[-2]["reverse"] is True and fake.calls[-2]["min_id"] == 399  # newer pass above the watermark
    assert fake.calls[-1]["max_id"] == 380  # then older, down to since

    bounds = db.message_bounds(-100, 157)
    assert bounds["count"] == 50 and bounds["oldest_id"] == 350
    assert db.message_bounds(-100, 1) is None  # General was never fetched
    assert db.forum_topics([-100])[-100] == {1: "General", 157: "Русский"}


def test_session_lock_is_exclusive_and_released(tmp_path) -> None:
    from tg_recall.telegram_client import SessionLock, TelegramBusyError

    first = SessionLock(str(tmp_path / "tg.session"))
    first.acquire()
    with pytest.raises(TelegramBusyError, match="busy"):
        SessionLock(str(tmp_path / "tg.session")).acquire()
    first.release()
    again = SessionLock(str(tmp_path / "tg.session"))
    again.acquire()
    again.release()


def _client_with(tmp_path, monkeypatch, fake):
    import tg_recall.telegram_client as module

    db = Database(tmp_path / "archive.sqlite3")
    db.migrate()
    client = TelegramArchiveClient(cfg(), db)
    monkeypatch.setattr(client, "_client", lambda: fake)
    monkeypatch.setattr(module, "_load_telethon", lambda: (object, FakeFloodWaitError))
    return client, db


class PlainChat(FakeForum):
    """A non-forum chat with ids 300..349; a flood after ``flood_after`` messages."""

    def __init__(self, flood_after=None):
        super().__init__()
        self.messages = [_msg(i, 1000 - i) for i in range(300, 350)]
        self.flood_after = flood_after

    async def get_entity(self, chat_id):
        return SimpleNamespace(forum=False)

    async def iter_messages(self, chat_id, **kwargs):
        count = 0
        async for message in super().iter_messages(chat_id, **kwargs):
            if self.flood_after is not None and count >= self.flood_after:
                raise FakeFloodWaitError(30)
            count += 1
            yield message


def test_refresh_fetches_only_newer_messages_oldest_first(tmp_path, monkeypatch) -> None:
    fake = PlainChat()
    client, db = _client_with(tmp_path, monkeypatch, fake)
    for message in fake.messages[:40]:
        db.upsert_message(message_from_record(message))

    result = asyncio.run(client.sync_chat(10))

    assert fake.calls == [{"reply_to": None, "min_id": 339, "max_id": 0, "reverse": True}]  # no walk into history
    assert result["fetched"] == 10 and result["complete"] is True
    assert db.get_sync_state(10)["last_synced_at"]


def test_first_sync_of_a_chat_stops_at_thirty_days(tmp_path, monkeypatch) -> None:
    fake = PlainChat()
    fake.messages = [_msg(1, 60 * 24 * 40), _msg(2, 60 * 24 * 10), _msg(3, 60)]
    for message in fake.messages:
        message.date = datetime.now(UTC) - (datetime(2026, 10, 1, tzinfo=UTC) - message.date)
    client, db = _client_with(tmp_path, monkeypatch, fake)

    result = asyncio.run(client.sync_chat(10))

    assert result["fetched"] == 2 and db.message_bounds(10)["oldest_id"] == 2  # FIRST_SYNC = 30 days


def test_flood_wait_keeps_fetched_rows_and_blocks_the_next_call(tmp_path, monkeypatch) -> None:

    fake = PlainChat(flood_after=5)
    client, db = _client_with(tmp_path, monkeypatch, fake)

    result = asyncio.run(client.sync_chat(10, since=datetime(2026, 1, 1, tzinfo=UTC)))

    assert result["complete"] is False and result["retry_after"] and result["fetched"] == 5
    assert db.message_bounds(10)["count"] == 5
    with pytest.raises(TelegramRetryPendingError):
        asyncio.run(client.sync_chat(10))


def message_from_record(message):
    from tg_recall.telegram_client import message_from_telethon

    return message_from_telethon(10, message)


def test_long_sync_reports_progress(tmp_path, monkeypatch) -> None:
    import tg_recall.telegram_client as module

    monkeypatch.setattr(module, "PROGRESS_SECONDS", 0.0)
    client, _ = _client_with(tmp_path, monkeypatch, PlainChat())
    seen = []

    asyncio.run(client.sync_many([(10, None)], since=datetime(2026, 1, 1, tzinfo=UTC), progress=seen.append))

    assert seen and seen[-1]["chat_id"] == 10 and seen[-1]["fetched"] == len(seen)
