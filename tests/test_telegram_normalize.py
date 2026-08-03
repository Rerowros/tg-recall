from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from tg_recall.telegram_client import chat_from_dialog, message_from_telethon


def test_chat_from_dialog_detects_group() -> None:
    dialog = SimpleNamespace(
        id=-100,
        name="Team",
        is_user=False,
        is_group=True,
        is_channel=False,
        entity=SimpleNamespace(username="team"),
    )

    chat = chat_from_dialog(dialog)

    assert chat.chat_id == -100
    assert chat.title == "Team"
    assert chat.chat_type == "group"
    assert chat.username == "team"


def test_message_from_telethon_normalizes_media_and_links() -> None:
    message = SimpleNamespace(
        id=42,
        date=datetime(2026, 1, 2, tzinfo=UTC),
        raw_text="See https://example.com",
        text=None,
        sender_id=7,
        sender=SimpleNamespace(first_name="Ada", last_name="Lovelace", username="ada"),
        reply_to=SimpleNamespace(reply_to_msg_id=41),
        fwd_from=None,
        edit_date=None,
        voice=True,
        audio=None,
        video=None,
        photo=None,
        document=None,
        media=True,
    )

    record = message_from_telethon(10, message)

    assert record.message_id == 42
    assert record.sender_name == "Ada Lovelace"
    assert record.reply_to_message_id == 41
    assert record.media_type == "voice"
    assert "https://example.com" in record.links_json
