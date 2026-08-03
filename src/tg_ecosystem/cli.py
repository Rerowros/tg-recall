from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .assistant import ArchiveAssistant
from .config import AppConfig, load_config, redact_config, save_config, set_config_value
from .media import MediaDownloader, MediaStore, copy_file_download
from .models import SearchFilters
from .security import CONFIRMATION_PHRASE, check_path_private, enforce_ai_archive_read, harden_path, is_automation_shell, require_human_confirmation
from .storage import Database
from .telegram_client import TelegramArchiveClient, run_async
from .transcription import TranscriptionService


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 2
    try:
        return args.handler(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tg-ecosystem")
    parser.add_argument("--config", help="Path to config.json")
    parser.add_argument("--confirm-risk", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command")

    setup = sub.add_parser("setup", help="Create local config and database")
    setup.set_defaults(handler=cmd_setup)

    security = sub.add_parser("security", help="Inspect and harden local data protection")
    security_sub = security.add_subparsers(dest="security_command")
    security_check = security_sub.add_parser("check", help="Check local archive/session file protection")
    security_check.add_argument("--fix", action="store_true", help="Apply best-effort private file permissions")
    security_check.set_defaults(handler=cmd_security_check)

    config = sub.add_parser("config", help="Manage configuration")
    config_sub = config.add_subparsers(dest="config_command")
    config_show = config_sub.add_parser("show", help="Show redacted configuration")
    config_show.set_defaults(handler=cmd_config_show)
    config_set = config_sub.add_parser("set", help="Set a configuration value")
    config_set.add_argument("key")
    config_set.add_argument("value")
    config_set.set_defaults(handler=cmd_config_set)

    telegram = sub.add_parser("telegram", help="Telegram session commands")
    telegram_sub = telegram.add_subparsers(dest="telegram_command")
    auth = telegram_sub.add_parser("auth", help="Authorize Telegram account")
    auth.set_defaults(handler=cmd_telegram_auth)
    check = telegram_sub.add_parser("check", help="Check Telegram session")
    check.set_defaults(handler=cmd_telegram_check)

    chats = sub.add_parser("chats", help="Chat discovery")
    chats_sub = chats.add_subparsers(dest="chats_command")
    chats_list = chats_sub.add_parser("list", help="Discover and list Telegram chats")
    chats_list.add_argument("--limit", type=int)
    chats_list.add_argument("--cached", action="store_true", help="Read cached chats from database")
    chats_list.set_defaults(handler=cmd_chats_list)

    scopes = sub.add_parser("scopes", help="Sync scopes")
    scopes_sub = scopes.add_subparsers(dest="scopes_command")
    scopes_create = scopes_sub.add_parser("create", help="Create or update a sync scope")
    scopes_create.add_argument("name")
    scopes_create.add_argument("--chat", type=int, action="append", required=True, dest="chats")
    scopes_create.add_argument("--since")
    scopes_create.add_argument("--until")
    scopes_create.set_defaults(handler=cmd_scope_create)
    scopes_list = scopes_sub.add_parser("list", help="List sync scopes")
    scopes_list.set_defaults(handler=cmd_scope_list)

    sync = sub.add_parser("sync", help="Run sync jobs")
    sync_sub = sync.add_subparsers(dest="sync_command")
    sync_run = sync_sub.add_parser("run", help="Sync messages for a scope")
    sync_run.add_argument("scope")
    sync_run.add_argument("--limit", type=int, default=100)
    sync_run.set_defaults(handler=cmd_sync_run)

    search = sub.add_parser("search", help="Search indexed archive")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--chat-id", type=int)
    search.add_argument("--sender-id", type=int)
    search.add_argument("--since")
    search.add_argument("--until")
    search.add_argument("--media-type")
    search.add_argument("--has-link", action="store_true")
    search.add_argument("--semantic", action="store_true")
    search.set_defaults(handler=cmd_search)

    ask = sub.add_parser("ask", help="Retrieve cited evidence for an AI task")
    ask.add_argument("query")
    ask.add_argument("--limit", type=int, default=10)
    ask.add_argument("--chat-id", type=int)
    ask.set_defaults(handler=cmd_ask)

    jobs = sub.add_parser("jobs", help="Inspect jobs")
    jobs.set_defaults(handler=cmd_jobs)

    media = sub.add_parser("media", help="Media maintenance")
    media_sub = media.add_subparsers(dest="media_command")
    media_usage = media_sub.add_parser("usage", help="Show media disk usage")
    media_usage.set_defaults(handler=cmd_media_usage)
    media_download = media_sub.add_parser("download", help="Download pending Telegram media jobs")
    media_download.add_argument("--limit", type=int, default=20)
    media_download.add_argument("--copy-from", help="Test helper: copy this file for each pending media job")
    media_download.set_defaults(handler=cmd_media_download)

    transcribe = sub.add_parser("transcribe", help="Run transcription jobs")
    transcribe_sub = transcribe.add_subparsers(dest="transcribe_command")
    transcribe_run = transcribe_sub.add_parser("run", help="Run pending transcription jobs")
    transcribe_run.add_argument("--limit", type=int, default=20)
    transcribe_run.add_argument("--telegram", action="store_true", help="Use Telegram Premium/account transcription API first")
    transcribe_run.set_defaults(handler=cmd_transcribe_run)

    index = sub.add_parser("index", help="Index maintenance")
    index_sub = index.add_subparsers(dest="index_command")
    index_rebuild = index_sub.add_parser("rebuild", help="Rebuild keyword and semantic indexes")
    index_rebuild.set_defaults(handler=cmd_index_rebuild)

    purge = sub.add_parser("purge", help="Delete local archive data")
    purge_group = purge.add_mutually_exclusive_group(required=True)
    purge_group.add_argument("--chat-id", type=int)
    purge_group.add_argument("--all", action="store_true")
    purge.set_defaults(handler=cmd_purge)

    return parser


def services(args: argparse.Namespace) -> tuple[AppConfig, Database]:
    cfg = load_config(args.config)
    cfg.ensure_dirs()
    db = Database(cfg.db_path)
    db.migrate()
    return cfg, db


def cmd_setup(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    cfg.ensure_dirs()
    path = save_config(cfg, args.config)
    Database(cfg.db_path).migrate()
    print(f"config: {path}")
    print(f"database: {Path(cfg.db_path).resolve()}")
    return 0


def cmd_config_show(args: argparse.Namespace) -> int:
    cfg, _ = services(args)
    print(json.dumps(redact_config(cfg), indent=2, ensure_ascii=False))
    return 0


def cmd_config_set(args: argparse.Namespace) -> int:
    require_human_confirmation("config set", args.confirm_risk)
    cfg = load_config(args.config)
    cfg = set_config_value(cfg, args.key, args.value)
    cfg.ensure_dirs()
    save_config(cfg, args.config)
    print(f"set {args.key}")
    return 0


def cmd_telegram_auth(args: argparse.Namespace) -> int:
    require_human_confirmation("telegram auth", args.confirm_risk)
    cfg, db = services(args)
    run_async(TelegramArchiveClient(cfg, db).authorize())
    print("authorized")
    return 0


def cmd_telegram_check(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    result = run_async(TelegramArchiveClient(cfg, db).check())
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_chats_list(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    if args.cached:
        if is_automation_shell() and not cfg.ai_access.enabled:
            raise PermissionError("cached chat listing is disabled for AI/automation mode")
        chats = db.list_chats()
        for chat in chats:
            if is_automation_shell() and chat["chat_id"] not in cfg.ai_access.allowed_chat_ids:
                continue
            print(f"{chat['chat_id']}\t{chat['chat_type']}\t{chat['title']}")
        return 0
    require_human_confirmation("live Telegram chat discovery", args.confirm_risk)
    chats = run_async(TelegramArchiveClient(cfg, db).discover_chats(limit=args.limit))
    for chat in chats:
        print(f"{chat.chat_id}\t{chat.chat_type}\t{chat.title}")
    return 0


def cmd_scope_create(args: argparse.Namespace) -> int:
    require_human_confirmation("scope create", args.confirm_risk)
    _, db = services(args)
    db.create_scope(args.name, args.chats, args.since, args.until)
    db.audit("scope_saved", args.name, chats=args.chats, since=args.since, until=args.until)
    print(f"scope saved: {args.name}")
    return 0


def cmd_scope_list(args: argparse.Namespace) -> int:
    _, db = services(args)
    for scope in db.list_scopes():
        print(json.dumps(scope, ensure_ascii=False))
    return 0


def cmd_sync_run(args: argparse.Namespace) -> int:
    require_human_confirmation("sync run", args.confirm_risk)
    cfg, db = services(args)
    result = run_async(TelegramArchiveClient(cfg, db).sync_scope(args.scope, limit=args.limit))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    limit = enforce_ai_archive_read(
        enabled=cfg.ai_access.enabled,
        allowed_chat_ids=cfg.ai_access.allowed_chat_ids,
        requested_chat_id=args.chat_id,
        requested_limit=args.limit,
        max_results=cfg.ai_access.max_results,
    )
    filters = SearchFilters(
        chat_id=args.chat_id,
        sender_id=args.sender_id,
        since=args.since,
        until=args.until,
        media_type=args.media_type,
        has_link=True if args.has_link else None,
    )
    if args.semantic:
        if not cfg.semantic.enabled:
            raise PermissionError("semantic search is disabled; set semantic.enabled true to use it")
        results = db.semantic_search(args.query, limit=limit, filters=filters)
    else:
        results = db.search(args.query, limit=limit, filters=filters)
    for item in results:
        print(f"{item.timestamp}\t{item.chat_title}\t{item.citation}\t{item.text}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    limit = enforce_ai_archive_read(
        enabled=cfg.ai_access.enabled,
        allowed_chat_ids=cfg.ai_access.allowed_chat_ids,
        requested_chat_id=args.chat_id,
        requested_limit=args.limit,
        max_results=cfg.ai_access.max_results,
    )
    answer = ArchiveAssistant(db, cfg).answer(args.query, limit=limit, chat_id=args.chat_id)
    print(answer)
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    _, db = services(args)
    with db.connect() as conn:
        for row in conn.execute(
            """
            SELECT id, stage, status, chat_id, message_id, media_id, error, retry_after, updated_at
            FROM jobs
            ORDER BY updated_at DESC
            LIMIT 50
            """
        ):
            print(dict(row))
    return 0


def cmd_media_usage(args: argparse.Namespace) -> int:
    cfg, _ = services(args)
    bytes_used = MediaStore(cfg.media_dir).disk_usage_bytes()
    print(json.dumps({"media_dir": str(Path(cfg.media_dir).resolve()), "bytes": bytes_used}, indent=2))
    return 0


def cmd_media_download(args: argparse.Namespace) -> int:
    require_human_confirmation("media download", args.confirm_risk)
    cfg, db = services(args)
    if args.copy_from:
        downloader = MediaDownloader(db, MediaStore(cfg.media_dir), copy_file_download(args.copy_from))
        result = downloader.run_pending(limit=args.limit)
    else:
        result = run_async(TelegramArchiveClient(cfg, db).download_pending_media(limit=args.limit))
    print(json.dumps(result, indent=2))
    return 0


def cmd_transcribe_run(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    if args.telegram:
        require_human_confirmation("Telegram transcription", args.confirm_risk)
        result = run_async(TelegramArchiveClient(cfg, db).transcribe_pending_with_telegram(limit=args.limit))
        print(json.dumps(result, indent=2))
        return 0
    if is_automation_shell() and not cfg.provider_policy.external_transcription_enabled:
        # Local sidecar transcription is still safe; this check documents the boundary.
        pass
    result = TranscriptionService(db).run_pending(limit=args.limit)
    print(json.dumps(result, indent=2))
    return 0


def cmd_index_rebuild(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    if is_automation_shell() and not cfg.ai_access.enabled:
        raise PermissionError("index rebuild is disabled for AI/automation mode")
    result = db.rebuild_indexes()
    db.audit("index_rebuild", details=result)
    print(json.dumps(result, indent=2))
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    require_human_confirmation("purge", args.confirm_risk, require_phrase_interactive=True)
    _, db = services(args)
    if args.all:
        db.purge_all()
        db.audit("purge_all")
        print("purged all local archive data")
        return 0
    result = db.purge_chat(args.chat_id)
    db.audit("purge_chat", chat_id=args.chat_id, result=result)
    print(json.dumps(result, indent=2))
    return 0


def cmd_security_check(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    cfg.ensure_dirs()
    paths = [
        (cfg.data_dir, True),
        (cfg.media_dir, True),
        (cfg.db_path, False),
        (cfg.telegram.session_path, False),
        (Path(cfg.telegram.session_path).with_suffix(".session"), False),
        (args.config, False) if args.config else (Path(cfg.data_dir) / "config.json", False),
    ]
    seen: set[str] = set()
    for raw_path, is_dir in paths:
        path = Path(raw_path)
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        finding = harden_path(path, is_dir=is_dir) if args.fix else check_path_private(path)
        print(f"{finding.status}\t{finding.path}\t{finding.detail}")
    print(f"automation_mode\t{is_automation_shell()}\tAI archive reads require ai_access.enabled and allowed_chat_ids")
    print(f"confirmation_phrase\t{CONFIRMATION_PHRASE}")
    return 0
