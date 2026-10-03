from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .agent_tools import AgentTools, ToolResult, allowed_chat_ids
from .agent_query import AgentQueryError
from .backup import create_backup, restore_backup
from .config import AppConfig, load_config, redact_config, save_config, set_config_value
from .media import MediaDownloader, MediaStore, copy_file_download
from .models import SearchFilters
from .security import (
    AgentOperation,
    AgentPolicyError,
    RequestedAgentScope,
    audit_policy_decision,
    check_path_private,
    harden_path,
    is_automation_shell,
    require_agent_policy,
    require_human_confirmation,
)
from .storage import Database, SchemaCompatibilityError
from .telegram_client import TelegramArchiveClient, run_async
from .transcription import FasterWhisperXXLProvider, TranscriptionService, WhisperCLIProvider


def main(argv: list[str] | None = None) -> int:
    # Agents read CLI output through pipes; on Windows those default to the ANSI
    # code page and any emoji in a chat title or message would abort the command.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raw_argv = _normalize_global_arguments(list(sys.argv[1:] if argv is None else argv))
    parser = build_parser()
    args = parser.parse_args(raw_argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 2
    try:
        args._agent_policy = _enforce_agent_command(args)
        return args.handler(args)
    except AgentPolicyError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": {"code": exc.error_code, "message": str(exc), "details": exc.details}}))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": {"code": type(exc).__name__, "message": str(exc)}}))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tg-recall",
        description="Local Telegram archive for you and your AI agents: sync, search, read, export.",
    )
    parser.add_argument("--config", help="Explicit config.json path")
    parser.add_argument("--home", help="Portable tg-recall root")
    parser.add_argument("--profile", help="Telegram archive profile")
    parser.add_argument("--json", action="store_true", help="Write machine-readable result to stdout")
    parser.add_argument("--confirm-risk", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("setup", help="Create local profile config and database").set_defaults(handler=cmd_setup)
    sub.add_parser("doctor", help="Check archive, schema, Telegram session and transcription").set_defaults(handler=cmd_doctor)

    config = sub.add_parser("config", help="Show or change configuration")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("show", help="Show redacted configuration").set_defaults(handler=cmd_config_show)
    config_set = config_sub.add_parser("set", help="Set a value, e.g. ai_access.allowed_chat_ids -100123,456")
    config_set.add_argument("key")
    config_set.add_argument("value")
    config_set.set_defaults(handler=cmd_config_set)

    telegram = sub.add_parser("telegram", help="Telegram session")
    telegram_sub = telegram.add_subparsers(dest="telegram_command", required=True)
    telegram_sub.add_parser("auth", help="Log in (interactive)").set_defaults(handler=cmd_telegram_auth)
    telegram_sub.add_parser("check", help="Check the saved session").set_defaults(handler=cmd_telegram_check)

    target_help = "chat id, title fragment, t.me link (t.me/name/<topic>) or '<chat>/<topic>'"
    chats = sub.add_parser("chats", help="List chats (with forum topics)")
    chats.add_argument("query", nargs="?", help="Title fragment")
    chats.add_argument("--all", action="store_true", help="Include chats without archived messages")
    chats.add_argument("--refresh", action="store_true", help="Fetch the chat list from Telegram first")
    chats.add_argument("--limit", type=int, help="With --refresh: max dialogs to fetch")
    chats.set_defaults(handler=cmd_chats)

    sync = sub.add_parser("sync", help="Download new messages; with --since also older history")
    sync.add_argument("chats", nargs="*", metavar="TARGET", help=f"{target_help}; default: allowlist or every archived chat")
    sync.add_argument("--since", help="Also fetch history back to this date (ISO, 30d, ...); new chats default to 30d")
    sync.add_argument("--max-seconds", type=float, default=3600.0)
    sync.add_argument("--media", default="none", help="Queue media for download: none, all or e.g. voice,audio")
    sync.set_defaults(handler=cmd_sync)

    search = sub.add_parser("search", help="Find messages with context and tg:// citations")
    search.add_argument("query")
    _add_read_scope(search, target_help)
    search.add_argument("--context", type=int, help="Messages around each hit (default 2)")
    search.add_argument("--limit", type=int, help="Max hits (default 10)")
    search.set_defaults(handler=cmd_search)

    read = sub.add_parser("read", help="Read new messages, a period, or around citations")
    read.add_argument("refs", nargs="*", metavar="REF", help="tg://chat/<id>/message/<id> or <chat>/<id>")
    _add_read_scope(read, target_help)
    read.add_argument("--before", type=int)
    read.add_argument("--after", type=int)
    read.add_argument("--full", action="store_true", help="Do not truncate long messages")
    read.add_argument("--limit", type=int)
    read.set_defaults(handler=cmd_read)

    export = sub.add_parser("export", help="Write one chat to JSONL")
    export.add_argument("--chat", type=int, required=True, dest="chat_id")
    export.add_argument("--since")
    export.add_argument("--until")
    export.add_argument("--include", default="transcripts,media-metadata")
    export.add_argument("--format", choices=["jsonl"], default="jsonl")
    export.add_argument("--output")
    export.add_argument("--limit", type=int, default=100000)
    export.set_defaults(handler=cmd_export)

    media = sub.add_parser("media", help="Media files")
    media_sub = media.add_subparsers(dest="media_command", required=True)
    media_sub.add_parser("usage", help="Show media disk usage").set_defaults(handler=cmd_media_usage)
    media_download = media_sub.add_parser("download", help="Download queued media (see sync --media)")
    media_download.add_argument("--limit", type=int, default=20)
    media_download.add_argument("--copy-from", help=argparse.SUPPRESS)
    media_download.set_defaults(handler=cmd_media_download)
    materialize = media_sub.add_parser("materialize", help="Download media of one cited message")
    materialize.add_argument("--citation", required=True)
    materialize.set_defaults(handler=cmd_media_materialize)

    transcribe = sub.add_parser("transcribe", help="Transcribe voice/audio/video")
    transcribe_sub = transcribe.add_subparsers(dest="transcribe_command", required=True)
    transcribe_run = transcribe_sub.add_parser("run", help="Run pending transcription jobs")
    transcribe_run.add_argument("--limit", type=int, default=20)
    transcribe_run.add_argument("--provider", choices=["sidecar", "telegram", "local", "auto"], default="sidecar")
    transcribe_run.add_argument("--telegram", action="store_true", help=argparse.SUPPRESS)
    transcribe_run.add_argument("--citation", help="Transcribe media of one cited message")
    transcribe_run.set_defaults(handler=cmd_transcribe_run)

    jobs = sub.add_parser("jobs", help="Inspect or repair the media/transcription queue")
    jobs.add_argument("--stage")
    jobs.add_argument("--status")
    jobs.add_argument("--retryable", choices=["true", "false"])
    jobs.add_argument("--chat-id", type=int)
    jobs.add_argument("--older-than", help="Only jobs updated before ISO-8601 timestamp")
    jobs.add_argument("--limit", type=int, default=50)
    jobs.add_argument("--retry", type=int, action="append", dest="retry_ids", help="Explicit job id to requeue")
    jobs.add_argument("--override-retry-after", action="store_true", help="Human-only override for an active backoff")
    jobs.add_argument("--repair", action="store_true", help="Preview deterministic queue repairs")
    jobs.add_argument("--apply", action="store_true", help="Apply --repair changes after preview")
    jobs.add_argument("--stale-after-hours", type=int, default=1)
    jobs.set_defaults(handler=cmd_jobs)

    index = sub.add_parser("index", help="Full-text index maintenance")
    index_sub = index.add_subparsers(dest="index_command", required=True)
    index_sub.add_parser("rebuild", help="Rebuild the full-text index").set_defaults(handler=cmd_index_rebuild)

    security = sub.add_parser("security", help="Check private file permissions")
    security_sub = security.add_subparsers(dest="security_command", required=True)
    security_check = security_sub.add_parser("check", help="Check archive/session file protection")
    security_check.add_argument("--fix", action="store_true", help="Apply best-effort private file permissions")
    security_check.set_defaults(handler=cmd_security_check)

    backup = sub.add_parser("backup", help="Create or restore a consistent local backup")
    backup_sub = backup.add_subparsers(dest="backup_command", required=True)
    backup_create = backup_sub.add_parser("create", help="Create a backup ZIP")
    backup_create.add_argument("--mode", choices=["essential", "full"], default="essential")
    backup_create.add_argument("--include-session", action="store_true")
    backup_create.add_argument("--output", required=True)
    backup_create.set_defaults(handler=cmd_backup_create)
    backup_restore = backup_sub.add_parser("restore", help="Restore a backup into a profile")
    backup_restore.add_argument("archive")
    backup_restore.add_argument("--replace", action="store_true")
    backup_restore.set_defaults(handler=cmd_backup_restore)

    purge = sub.add_parser("purge", help="Delete local archive data")
    purge_group = purge.add_mutually_exclusive_group(required=True)
    purge_group.add_argument("--chat-id", type=int)
    purge_group.add_argument("--all", action="store_true")
    purge.set_defaults(handler=cmd_purge)
    return parser


def _add_read_scope(parser: argparse.ArgumentParser, target_help: str) -> None:
    parser.add_argument("--chat", action="append", dest="chats", metavar="TARGET", help=f"{target_help}; repeatable")
    parser.add_argument("--since", help="ISO date/time or 7d, 24h, today, yesterday")
    parser.add_argument("--until")
    parser.add_argument("--from", dest="sender", help="Sender name fragment, user id or 'me'")
    parser.add_argument("--media", choices=["voice", "audio", "photo", "video", "document", "any"])
    parser.add_argument("--budget", type=int, help="Max output tokens")


# Commands that never run from an agent shell. search/read/chats/sync enforce
# the AI policy themselves (AgentTools), so the CLI gate lets them through.
_HUMAN_ONLY = {"config", "setup", "purge", "backup", "index", "telegram", "security", "jobs"}
_AGENT_TOOLS = {"search", "read", "chats", "sync"}


def _enforce_agent_command(args: argparse.Namespace):
    """Apply the one operation table before a handler opens Telegram or writes data."""

    operation, requested = _agent_operation_and_scope(args)
    if operation is None:
        return None
    cfg = load_config(args.config, home=args.home, profile=args.profile)
    policy = cfg.ai_access
    allowed = list(policy.allowed_chat_ids)
    if policy.allow_all_chats and Path(cfg.db_path).exists():
        allowed = list(allowed_chat_ids(cfg, Database(cfg.db_path)))
    try:
        decision = require_agent_policy(
            operation,
            enabled=policy.enabled,
            allowed_chat_ids=allowed,
            max_results=policy.max_results,
            allowed_since=policy.allowed_since,
            allowed_until=policy.allowed_until,
            allowed_media_types=policy.allowed_media_types,
            requested=requested,
        )
    except AgentPolicyError as exc:
        _best_effort_policy_audit(cfg, exc.decision, requested)
        raise
    if is_automation_shell():
        _best_effort_policy_audit(cfg, decision, requested)
    return decision


def _agent_operation_and_scope(args: argparse.Namespace) -> tuple[AgentOperation | None, RequestedAgentScope]:
    command = args.command
    if command in _AGENT_TOOLS:
        if command == "chats" and args.refresh:
            return AgentOperation.HUMAN_ONLY, RequestedAgentScope()
        return None, RequestedAgentScope()
    if command in _HUMAN_ONLY or (command == "media" and args.media_command in {"usage", "download"}):
        return AgentOperation.HUMAN_ONLY, RequestedAgentScope()
    if command == "doctor":
        return AgentOperation.METADATA_LIST, RequestedAgentScope()
    if command == "export":
        return (
            AgentOperation.ARCHIVE_EXPORT,
            RequestedAgentScope(chat_ids=(args.chat_id,), since=args.since, until=args.until, result_limit=args.limit),
        )
    if command == "media" and args.media_command == "materialize":
        return AgentOperation.MEDIA_MATERIALIZE, RequestedAgentScope(chat_ids=_citation_chat(args.citation))
    if command == "transcribe" and args.citation:
        return AgentOperation.TRANSCRIBE, RequestedAgentScope(chat_ids=_citation_chat(args.citation))
    return AgentOperation.HUMAN_ONLY, RequestedAgentScope()


def _citation_chat(citation: str) -> tuple[int, ...]:
    try:
        return (_parse_citation(citation)[0],)
    except ValueError:
        return ()


def _tools(args: argparse.Namespace) -> AgentTools:
    cfg, db = services(args)
    return AgentTools(cfg, db, client="cli", owner=not is_automation_shell())


def _emit_tool(args: argparse.Namespace, result: ToolResult) -> int:
    if args.json:
        return emit(args, {"text": result.text, "count": result.count, "chat_ids": list(result.chat_ids)})
    print(result.text)
    return 0


def _tool_args(args: argparse.Namespace, *names: str) -> dict[str, Any]:
    values = {name: getattr(args, name, None) for name in names}
    values["from"] = getattr(args, "sender", None)
    return {key: value for key, value in values.items() if value not in (None, [], False)}


def _run_tool(args: argparse.Namespace, name: str, tool_args: dict[str, Any]) -> int:
    try:
        return _emit_tool(args, getattr(_tools(args), name)(tool_args))
    except AgentQueryError as exc:
        # "chat_not_allowed: ..." carries a stable code; other messages are plain usage errors.
        message = str(exc)
        prefix = message.split(":", 1)[0]
        code = prefix if prefix.isidentifier() and prefix.islower() and "_" in prefix else "invalid_request"
        if args.json:
            print(json.dumps({"ok": False, "error": {"code": code, "message": message}}, ensure_ascii=False))
        else:
            print(f"error: {message}", file=sys.stderr)
        return 1


def cmd_chats(args: argparse.Namespace) -> int:
    if args.refresh:
        require_human_confirmation("live Telegram chat discovery", args.confirm_risk)
        cfg, db = services(args)
        run_async(TelegramArchiveClient(cfg, db).discover_chats(limit=args.limit))
    return _run_tool(args, "chats", _tool_args(args, "query", "all"))


def cmd_sync(args: argparse.Namespace) -> int:
    return _run_tool(args, "sync", {**_tool_args(args, "chats", "since", "max_seconds"), "media": args.media})


def cmd_search(args: argparse.Namespace) -> int:
    return _run_tool(args, "search", _tool_args(args, "query", "chats", "since", "until", "media", "context", "limit", "budget"))


def cmd_read(args: argparse.Namespace) -> int:
    return _run_tool(
        args, "read", _tool_args(args, "refs", "chats", "since", "until", "media", "before", "after", "full", "limit", "budget")
    )


def _normalize_global_arguments(argv: list[str]) -> list[str]:
    globals_: list[str] = []
    remaining: list[str] = []
    options_with_value = {"--config", "--home", "--profile", "--confirm-risk"}
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--json":
            globals_.append(token)
        elif token in options_with_value:
            if index + 1 >= len(argv):
                remaining.append(token)
            else:
                globals_.extend([token, argv[index + 1]])
                index += 1
        elif any(token.startswith(f"{option}=") for option in options_with_value):
            globals_.append(token)
        else:
            remaining.append(token)
        index += 1
    return [*globals_, *remaining]


def services(args: argparse.Namespace) -> tuple[AppConfig, Database]:
    cfg = load_config(args.config, home=args.home, profile=args.profile)
    cfg.ensure_dirs()
    db = Database(cfg.db_path)
    db.migrate()
    return cfg, db


def _best_effort_policy_audit(cfg: AppConfig, decision: Any, requested: RequestedAgentScope) -> None:
    db_path = Path(cfg.db_path)
    if not db_path.exists():
        return
    try:
        audit_policy_decision(Database(db_path), decision, requested=requested)
    except Exception:
        # A policy denial must not create an archive or hide the stable error
        # because an old/corrupt database cannot yet accept audit records.
        return


def emit(args: argparse.Namespace, value: Any, plain: str | None = None) -> int:
    if args.json:
        # Agents pay per token; humans get the indented form.
        indent = None if is_automation_shell() else 2
        print(json.dumps(value, ensure_ascii=False, indent=indent, default=str))
    elif plain is not None:
        print(plain)
    elif isinstance(value, str):
        print(value)
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    cfg = load_config(args.config, home=args.home, profile=args.profile)
    cfg.ensure_dirs()
    path = save_config(cfg, args.config, home=args.home)
    Database(cfg.db_path).migrate()
    return emit(args, {"config": str(path), "database": cfg.db_path, "profile": cfg.profile})


def cmd_config_show(args: argparse.Namespace) -> int:
    cfg, _ = services(args)
    return emit(args, redact_config(cfg))


def cmd_config_set(args: argparse.Namespace) -> int:
    require_human_confirmation("config set", args.confirm_risk)
    cfg = load_config(args.config, home=args.home, profile=args.profile)
    cfg = set_config_value(cfg, args.key, args.value)
    cfg.ensure_dirs()
    save_config(cfg, args.config, home=args.home)
    return emit(args, {"set": args.key})


def cmd_telegram_auth(args: argparse.Namespace) -> int:
    require_human_confirmation("telegram auth", args.confirm_risk)
    cfg, db = services(args)
    run_async(TelegramArchiveClient(cfg, db).authorize())
    return emit(args, {"authorized": True})


def cmd_telegram_check(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    return emit(args, run_async(TelegramArchiveClient(cfg, db).check()))


def cmd_export(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    filters = _filters_from_args(args, args._agent_policy)
    limit = args._agent_policy.result_limit or args.limit if is_automation_shell() else args.limit
    items = db.export_messages(filters, limit=limit)
    includes = {part.strip() for part in args.include.split(",") if part.strip()}
    if "media-metadata" in includes or "transcripts" in includes:
        for item in items:
            media = db.media_for_message(item["chat_id"], item["message_id"])
            if "media-metadata" in includes:
                item["media"] = [
                    {"id": value.id, "type": value.media_type, "status": value.status, "sha256": value.sha256}
                    for value in media
                ]
            if "transcripts" in includes:
                item["transcripts"] = _transcripts_for_message(db, media)
    target = Path(args.output) if args.output else Path(cfg.exports_dir) / f"chat-{args.chat_id}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    target = target.expanduser().resolve()
    if is_automation_shell():
        exports_root = Path(cfg.exports_dir).resolve()
        try:
            target.relative_to(exports_root)
        except ValueError as exc:
            raise AgentPolicyError(
                _policy_denial(args._agent_policy, "private_export_path_required", "automation exports must remain inside the profile exports directory")
            ) from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    harden_path(target, is_dir=False)
    return emit(args, {"path": str(target), "messages": len(items), "format": args.format})


def cmd_jobs(args: argparse.Namespace) -> int:
    _, db = services(args)
    if args.retry_ids:
        if args.override_retry_after:
            require_human_confirmation("jobs retry override", args.confirm_risk, require_phrase_interactive=True)
        return emit(args, db.retry_jobs(args.retry_ids, override_retry_after=args.override_retry_after))
    if args.repair:
        if args.apply:
            require_human_confirmation("jobs repair --apply", args.confirm_risk, require_phrase_interactive=True)
        return emit(args, db.repair_jobs(apply=args.apply, stale_after_hours=args.stale_after_hours))
    retryable = None if args.retryable is None else args.retryable == "true"
    return emit(args, db.list_jobs(stage=args.stage, status=args.status, retryable=retryable, chat_id=args.chat_id, older_than=args.older_than, limit=args.limit))


def cmd_media_usage(args: argparse.Namespace) -> int:
    cfg, _ = services(args)
    store = MediaStore(cfg.media_dir, Path(cfg.cache_dir) / "downloads")
    return emit(args, {"bytes": store.disk_usage_bytes()})


def cmd_media_download(args: argparse.Namespace) -> int:
    require_human_confirmation("media download", args.confirm_risk)
    cfg, db = services(args)
    if args.copy_from:
        downloader = MediaDownloader(db, MediaStore(cfg.media_dir, Path(cfg.cache_dir) / "downloads"), copy_file_download(args.copy_from))
        result = downloader.run_pending(limit=args.limit)
    else:
        result = run_async(TelegramArchiveClient(cfg, db).download_pending_media(limit=args.limit))
    return emit(args, result)


def cmd_media_materialize(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    chat_id, message_id = _parse_citation(args.citation)
    media = db.media_for_message(chat_id, message_id)
    if not media:
        raise ValueError("no archived media for citation; run sync ensure with an appropriate media policy first")
    if is_automation_shell() and not _media_policy_allows(args._agent_policy.media_policy, [value.media_type for value in media]):
        raise AgentPolicyError(_policy_denial(args._agent_policy, "media_not_allowed", "cited media is outside AI/automation media policy"))
    if any(value.status != "downloaded" for value in media):
        requested_ids = {value.id for value in media if value.status != "downloaded"}
        for media_id in requested_ids:
            db.requeue_media_download(media_id)
        run_async(TelegramArchiveClient(cfg, db).download_pending_media(limit=len(requested_ids), media_ids=requested_ids))
        media = db.media_for_message(chat_id, message_id)
    store = MediaStore(cfg.media_dir, Path(cfg.cache_dir) / "downloads")
    return emit(
        args,
        {
            "citation": args.citation,
            "media": [
                {"type": value.media_type, "status": value.status, "path": str(store.path_for_media(value)) if store.path_for_media(value) else None}
                for value in media
            ],
        },
    )


def cmd_transcribe_run(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    policy = "telegram" if args.telegram else args.provider
    media_ids = None
    if args.citation:
        chat_id, message_id = _parse_citation(args.citation)
        media = db.media_for_message(chat_id, message_id)
        if not media:
            raise ValueError("no archived media for citation")
        if is_automation_shell() and not _media_policy_allows(args._agent_policy.media_policy, [value.media_type for value in media]):
            raise AgentPolicyError(_policy_denial(args._agent_policy, "media_not_allowed", "cited media is outside AI/automation media policy"))
        media_ids = {value.id for value in media}
    result = _run_transcription_policy(args, cfg, db, TelegramArchiveClient(cfg, db), policy, args.limit, media_ids)
    return emit(args, result)


def cmd_index_rebuild(args: argparse.Namespace) -> int:
    _, db = services(args)
    if is_automation_shell():
        raise PermissionError("index rebuild is not available to automation; use agent retrieval")
    return emit(args, db.rebuild_indexes())


def cmd_backup_create(args: argparse.Namespace) -> int:
    cfg, _ = services(args)
    return emit(args, create_backup(cfg, args.output, mode=args.mode, include_session=args.include_session))


def cmd_backup_restore(args: argparse.Namespace) -> int:
    if args.replace:
        require_human_confirmation("backup restore --replace", args.confirm_risk, require_phrase_interactive=True)
    profile = args.profile or "restored"
    return emit(args, restore_backup(args.archive, home=args.home, profile=profile, replace=args.replace))


def cmd_purge(args: argparse.Namespace) -> int:
    require_human_confirmation("purge", args.confirm_risk, require_phrase_interactive=True)
    _, db = services(args)
    if args.all:
        db.purge_all()
        return emit(args, {"purged": "all"})
    return emit(args, db.purge_chat(args.chat_id))


def cmd_security_check(args: argparse.Namespace) -> int:
    cfg, _ = services(args)
    paths = [
        (cfg.data_dir, True), (cfg.media_dir, True), (cfg.db_path, False), (cfg.telegram.session_path, False),
        (cfg.credentials_path, False), (cfg.state_dir, True), (cfg.cache_dir, True),
    ]
    findings = []
    for raw_path, is_dir in paths:
        path = Path(raw_path)
        finding = harden_path(path, is_dir=is_dir) if args.fix else check_path_private(path)
        findings.append({"path": finding.path, "status": finding.status, "detail": finding.detail})
    return emit(args, {"findings": findings, "automation_mode": is_automation_shell()})


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = load_config(args.config, home=args.home, profile=args.profile)
    cfg.ensure_dirs()
    db = Database(cfg.db_path)
    try:
        db.migrate()
    except SchemaCompatibilityError as exc:
        return emit(
            args,
            {
                "profile": cfg.profile,
                "maintenance": {"schema": {"status": "incompatible", "detail": str(exc)}},
                "backup_guidance": "Do not downgrade the archive. Create a backup and use a newer tg-recall release.",
            },
        )
    status: dict[str, Any] = {
        "profile": cfg.profile,
        "archive": db.health(),
        "maintenance": db.diagnostics(),
        "providers": {"ffmpeg": shutil.which("ffmpeg") is not None, "whisper": WhisperCLIProvider.available()},
        "local_transcription": _local_transcription_diagnostics(cfg),
        "session_present": Path(cfg.telegram.session_path).exists(),
        "free_bytes": shutil.disk_usage(Path(cfg.data_dir)).free,
    }
    if cfg.telegram.api_id and cfg.telegram.api_hash and status["session_present"]:
        try:
            status["telegram"] = {"authorized": bool(run_async(TelegramArchiveClient(cfg, db).check()).get("authorized"))}
        except Exception as exc:
            status["telegram"] = {"authorized": False, "error": type(exc).__name__}
    else:
        status["telegram"] = {"authorized": False, "reason": "credentials or session missing"}
    status["backup_guidance"] = "Create an essential backup before schema or queue repair: tg-recall backup create --mode essential --output PATH"
    return emit(args, status)


def _run_transcription_policy(
    args: argparse.Namespace,
    cfg: AppConfig,
    db: Database,
    client: TelegramArchiveClient,
    policy: str,
    limit: int,
    media_ids: set[int] | None = None,
) -> dict[str, Any]:
    if policy == "off":
        return {"skipped": True}
    if policy == "telegram":
        return {"telegram": run_async(client.transcribe_pending_with_telegram(limit=limit, media_ids=media_ids))}
    if policy == "local":
        return {"local": _run_local_whisper(cfg, db, limit, media_ids)}
    telegram = run_async(client.transcribe_pending_with_telegram(limit=limit, media_ids=media_ids))
    return {"telegram": telegram, "local": _run_local_whisper(cfg, db, limit, media_ids)}


def _run_local_whisper(cfg: AppConfig, db: Database, limit: int, media_ids: set[int] | None = None) -> dict[str, int]:
    store = MediaStore(cfg.media_dir, Path(cfg.cache_dir) / "downloads")
    provider = _local_transcription_provider(cfg)
    return TranscriptionService(
        db, fallback_provider=provider, media_store=store, cache_dir=Path(cfg.cache_dir) / "extracted-audio"
    ).run_pending(limit=limit, media_ids=media_ids)


def _local_transcription_provider(cfg: AppConfig) -> WhisperCLIProvider | FasterWhisperXXLProvider:
    """Build the profile-selected local provider without accepting shell syntax."""

    settings = cfg.transcription
    output_dir = Path(cfg.cache_dir) / "transcription"
    if settings.backend == "whisper-cli":
        return WhisperCLIProvider(
            output_dir,
            executable=settings.executable,
            model=settings.model,
            language=settings.language,
            device=settings.device,
            compute_type=settings.compute_type,
            vad_filter=settings.vad_filter,
            timeout_seconds=settings.timeout_seconds,
        )
    if settings.backend == "faster-whisper-xxl":
        if settings.model is None:
            raise RuntimeError("Faster-Whisper-XXL requires transcription.model")
        return FasterWhisperXXLProvider(
            output_dir,
            executable=settings.executable,
            model=settings.model,
            model_dir=settings.model_dir,
            language=settings.language,
            device=settings.device,
            compute_type=settings.compute_type,
            vad_filter=settings.vad_filter,
            timeout_seconds=settings.timeout_seconds,
        )
    raise RuntimeError("Unsupported configured local transcription backend")


def _local_transcription_diagnostics(cfg: AppConfig) -> dict[str, Any]:
    """Report provider readiness without exposing configured host paths."""

    settings = cfg.transcription
    default_executable = "whisper" if settings.backend == "whisper-cli" else "faster-whisper-xxl"
    configured = settings.executable
    executable_name = _sanitized_executable_name(configured) if configured else default_executable
    resolved_executable = _diagnostic_executable_path(configured, default_executable)
    model_available: bool | None = None
    if settings.backend == "faster-whisper-xxl":
        if settings.model is not None and resolved_executable is not None:
            root = (
                Path(settings.model_dir).expanduser() if settings.model_dir else resolved_executable.parent / "_models"
            )
            model_available = (root / f"faster-whisper-{settings.model}").is_dir()
        else:
            model_available = False
    try:
        _local_transcription_provider(cfg)
        options_valid = True
    except (RuntimeError, ValueError):
        options_valid = False
    ready = bool(resolved_executable) and model_available is not False and options_valid
    return {
        "backend": settings.backend,
        "executable": {
            "name": executable_name,
            "configured": configured is not None,
            "resolved": resolved_executable is not None,
        },
        "model": {
            "name": settings.model,
            "configured": settings.model is not None,
            "local_available": model_available,
        },
        "options_valid": options_valid,
        "ready": ready,
    }


def _diagnostic_executable_path(configured: str | None, default_name: str) -> Path | None:
    """Resolve only for booleans; callers must never serialize the returned path."""

    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        resolved = shutil.which(configured)
    else:
        resolved = shutil.which(default_name)
    return Path(resolved).resolve() if resolved else None


def _sanitized_executable_name(configured: str) -> str:
    """Return a basename for either Windows or POSIX-style configured paths."""

    return configured.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1]


def _filters_from_args(args: argparse.Namespace, policy: Any | None = None) -> SearchFilters:
    automated = is_automation_shell() and policy is not None
    return SearchFilters(
        chat_id=policy.chat_ids[0] if automated and policy.chat_ids else getattr(args, "chat_id", None),
        sender_id=getattr(args, "sender_id", None),
        since=policy.since if automated else getattr(args, "since", None),
        until=policy.until if automated else getattr(args, "until", None),
        media_type=getattr(args, "media_type", None) if not automated else None,
        media_types=_media_filter_types(policy.media_policy) if automated else None,
        has_link=True if getattr(args, "has_link", False) else None,
    )


def _media_filter_types(policy: str | None) -> tuple[str, ...] | None:
    if policy is None or policy == "all":
        return None
    if policy == "none":
        return ()
    return tuple(sorted(value.strip() for value in policy.split(",") if value.strip()))


def _media_policy_allows(policy: str | None, media_types: list[str]) -> bool:
    if policy in {None, "all"}:
        return True
    allowed = {value.strip() for value in policy.split(",") if value.strip()}
    return all(value in allowed for value in media_types)


def _policy_denial(decision: Any, code: str, message: str):
    from .security import PolicyDecision

    return PolicyDecision(operation=decision.operation, allowed=False, error_code=code, message=message)


def _parse_citation(value: str) -> tuple[int, int]:
    prefix = "tg://chat/"
    if not value.startswith(prefix) or "/message/" not in value:
        raise ValueError("citation must be tg://chat/CHAT_ID/message/MESSAGE_ID")
    chat, message = value.removeprefix(prefix).split("/message/", 1)
    return int(chat), int(message)


def _transcripts_for_message(db: Database, media: list[Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    with db.connect() as conn:
        for item in media:
            rows = conn.execute("SELECT provider, text, language, created_at FROM transcripts WHERE media_id = ?", (item.id,)).fetchall()
            values.extend(dict(row) for row in rows)
    return values
