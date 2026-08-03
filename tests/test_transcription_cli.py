from __future__ import annotations

import json
from datetime import UTC, datetime

from tg_recall import cli
from tg_recall.cli import build_parser, main
from tg_recall.config import AppConfig, TranscriptionConfig, save_config
from tg_recall.models import ChatRecord, MessageRecord
from tg_recall.security import AgentOperation
from tg_recall.storage import Database


def test_local_provider_factory_passes_spaced_executable_as_one_typed_value(tmp_path, monkeypatch) -> None:
    cfg = AppConfig.default(tmp_path / "home")
    executable = tmp_path / "Program Files" / "faster-whisper-xxl.exe"
    observed: dict[str, object] = {}

    class Provider:
        def __init__(self, output_dir, **kwargs) -> None:
            observed["output_dir"] = output_dir
            observed.update(kwargs)

    monkeypatch.setattr(cli, "FasterWhisperXXLProvider", Provider)
    cfg.transcription = TranscriptionConfig(
        backend="faster-whisper-xxl",
        executable=str(executable),
        model="large-v3-turbo",
        model_dir=str(tmp_path / "local models"),
        language="ru",
        device="cuda",
        compute_type="float16",
        vad_filter=False,
        timeout_seconds=900,
    )

    cli._local_transcription_provider(cfg)

    assert observed["executable"] == str(executable)
    assert observed["model_dir"] == str(tmp_path / "local models")
    assert observed["model"] == "large-v3-turbo"
    assert observed["timeout_seconds"] == 900


def test_doctor_adds_sanitized_local_transcription_diagnostics(tmp_path, capsys) -> None:
    home = tmp_path / "home"
    executable = tmp_path / "private install" / "faster-whisper-xxl.exe"
    executable.parent.mkdir()
    executable.write_text("", encoding="utf-8")
    model_root = tmp_path / "private models"
    (model_root / "faster-whisper-large-v3-turbo").mkdir(parents=True)
    cfg = AppConfig.default(home)
    cfg.transcription = TranscriptionConfig(
        backend="faster-whisper-xxl",
        executable=str(executable),
        model="large-v3-turbo",
        model_dir=str(model_root),
        language="ru",
        device="cuda",
        compute_type="float16",
        vad_filter=False,
    )
    save_config(cfg, home=home)
    Database(cfg.db_path).migrate()

    assert main(["--home", str(home), "--json", "doctor"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert "whisper" in payload["providers"]
    assert payload["local_transcription"] == {
        "backend": "faster-whisper-xxl",
        "executable": {"name": "faster-whisper-xxl.exe", "configured": True, "resolved": True},
        "model": {"name": "large-v3-turbo", "configured": True, "local_available": True},
        "options_valid": True,
        "ready": True,
    }
    rendered = json.dumps(payload)
    assert str(executable.parent) not in rendered
    assert str(model_root) not in rendered


def test_diagnostics_sanitize_windows_executable_path_on_every_platform() -> None:
    cfg = AppConfig.default("unused")
    windows_path = r"C:\Private Install\faster-whisper-xxl.exe"
    cfg.transcription = TranscriptionConfig(
        backend="faster-whisper-xxl",
        executable=windows_path,
        model="large-v3-turbo",
    )

    diagnostics = cli._local_transcription_diagnostics(cfg)

    assert diagnostics["executable"]["name"] == "faster-whisper-xxl.exe"
    assert windows_path not in json.dumps(diagnostics)


def test_auto_runs_telegram_first_then_local_only_for_still_pending_jobs(tmp_path, monkeypatch) -> None:
    cfg = AppConfig.default(tmp_path / "home")
    db = Database(cfg.db_path)
    db.migrate()
    db.upsert_chat(ChatRecord(chat_id=10, title="Work", chat_type="group"))
    for message_id in (1, 2):
        db.upsert_message(
            MessageRecord(
                chat_id=10,
                message_id=message_id,
                date=datetime(2026, 1, message_id, tzinfo=UTC),
                text="voice",
                has_media=True,
                media_type="voice",
            )
        )
        media_id = db.enqueue_media(10, message_id, "voice", f"file-{message_id}")
        db.enqueue_transcription(media_id)

    class TelegramFirst:
        async def transcribe_pending_with_telegram(self, **kwargs):
            job = db.get_pending_jobs("transcription", limit=1)[0]
            db.update_job(job.id, "done")
            return {"completed": 1, "failed": 0, "skipped": 0}

    def local_fallback(_cfg, local_db, _limit, _media_ids):
        return {"pending_media_ids": [job.media_id for job in local_db.get_pending_jobs("transcription", limit=20)]}

    monkeypatch.setattr(cli, "_run_local_whisper", local_fallback)
    result = cli._run_transcription_policy(None, cfg, db, TelegramFirst(), "auto", 20)

    assert result["telegram"]["completed"] == 1
    assert len(result["local"]["pending_media_ids"]) == 1


def test_agent_transcription_requires_exact_citation_and_config_stays_human_only() -> None:
    parser = build_parser()

    uncited = parser.parse_args(["transcribe", "run", "--provider", "local"])
    operation, scope = cli._agent_operation_and_scope(uncited)
    assert operation is AgentOperation.HUMAN_ONLY
    assert scope.chat_ids == ()

    cited = parser.parse_args(["transcribe", "run", "--provider", "local", "--citation", "tg://chat/10/message/7"])
    operation, scope = cli._agent_operation_and_scope(cited)
    assert operation is AgentOperation.TRANSCRIBE
    assert scope.chat_ids == (10,)

    config = parser.parse_args(["config", "set", "transcription.model", "large-v3-turbo"])
    operation, _ = cli._agent_operation_and_scope(config)
    assert operation is AgentOperation.HUMAN_ONLY
