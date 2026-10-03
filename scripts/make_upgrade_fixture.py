"""Build an upgrade fixture: a backup made by an old tg-recall release.

Checks out the release tag into a temporary git worktree, fills a fresh
profile there with the synthetic Acme archive using that release's own code,
and writes its essential backup to ``tests/fixtures/upgrade/<tag>.zip``.
``tests/test_upgrade_compat.py`` restores every such backup with the current
code and checks that nothing was lost.

    uv run python scripts/make_upgrade_fixture.py v0.8.1

Add a fixture for every release that changes the database schema, the
profile config or the backup format. Fixtures hold only synthetic data.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Runs inside the old release's environment, so it may only use APIs that
# every supported release has (v0.2.0 onwards).
FILL = r"""
import sys
from datetime import UTC, datetime
from pathlib import Path

import tg_recall
from tg_recall.backup import create_backup
from tg_recall.config import AppConfig, save_config
from tg_recall.models import ChatRecord, MessageRecord
from tg_recall.storage import Database

home, output = Path(sys.argv[1]), Path(sys.argv[2])
GROUP, CHANNEL = -1001234567890, -1009876543210
config = AppConfig.default(home, "default")
config.ensure_dirs()
config.ai_access.enabled = True
config.ai_access.allowed_chat_ids = [GROUP, CHANNEL]
config.ai_access.max_results = 15
save_config(config, home=home)

db = Database(config.db_path)
db.migrate()
db.upsert_chat(ChatRecord(chat_id=GROUP, title="AcmeChat", chat_type="supergroup", username="acmechat"))
db.upsert_chat(ChatRecord(chat_id=CHANNEL, title="Acme News", chat_type="channel"))
topics = "topic_id" in MessageRecord.__dataclass_fields__
rows = [
    (GROUP, 1, "2026-06-03T09:00:00", "Payments are down again, card declined", 101, "Alice", None, 7),
    (GROUP, 2, "2026-06-03T09:05:00", "Same here, payment failed twice", 102, "Bob", 1, 7),
    (GROUP, 3, "2026-06-03T09:40:00", "Payments work again, sorry for the outage", 100, "Acme Support", 2, 7),
    (GROUP, 4, "2026-07-11T18:00:00", "Speed is slow on mobile tonight https://status.example.com", 101, "Alice", None, None),
    (GROUP, 5, "2026-07-11T18:02:00", "", 102, "Bob", None, None),
    (GROUP, 6, "2026-08-20T12:00:00", "Оплата картой снова не проходит", 103, "Алиса", None, 7),
    (CHANNEL, 10, "2026-06-03T10:00:00", "Payments are restored. Thanks for waiting.", None, "Acme News", None, None),
    (CHANNEL, 11, "2026-08-01T10:00:00", "New servers in Estonia are live.", None, "Acme News", None, None),
]
for chat_id, message_id, date, text, sender_id, sender, reply_to, topic_id in rows:
    extra = {"topic_id": topic_id} if topics else {}
    voice = chat_id == GROUP and message_id == 5
    db.upsert_message(
        MessageRecord(
            chat_id=chat_id,
            message_id=message_id,
            date=datetime.fromisoformat(date).replace(tzinfo=UTC),
            text=text,
            sender_id=sender_id,
            sender_name=sender,
            reply_to_message_id=reply_to,
            has_media=voice,
            media_type="voice" if voice else None,
            links_json='["https://status.example.com"]' if "https://" in text else "[]",
            **extra,
        )
    )
if hasattr(db, "upsert_forum_topics"):
    db.upsert_forum_topics(GROUP, [(7, "Payments")])
media_id = db.enqueue_media(GROUP, 5, "voice", "file-acme-5")
db.update_media_downloaded(media_id, sha256="0" * 64, size_bytes=4096, mime_type="audio/ogg", enqueue_transcription=False)
db.insert_transcript(media_id, "whisper-cli", "Голосовое: оплата снова работает, проверьте", language="ru")
if hasattr(db, "set_meta"):
    db.set_meta("self_user_id", "100")
create_backup(config, output, mode="essential")
print(tg_recall.__version__)
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("tag", help="release tag, e.g. v0.8.1")
    args = parser.parse_args(argv)
    output = ROOT / "tests" / "fixtures" / "upgrade" / f"{args.tag}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tg-recall-fixture-") as temporary:
        work = Path(temporary)
        tree = work / "tree"
        subprocess.run(["git", "worktree", "add", "--detach", str(tree), args.tag], cwd=ROOT, check=True)
        try:
            script = work / "fill.py"
            script.write_text(FILL, encoding="utf-8")
            output.unlink(missing_ok=True)
            subprocess.run(
                [
                    "uv",
                    "run",
                    "--quiet",
                    "--project",
                    str(tree),
                    "python",
                    str(script),
                    str(work / "home"),
                    str(output),
                ],
                check=True,
            )
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=ROOT, check=True)
    print(f"wrote {output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
