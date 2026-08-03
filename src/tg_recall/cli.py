from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .assistant import ArchiveAssistant
from .backup import create_backup, restore_backup
from .config import AppConfig, load_config, redact_config, save_config, set_config_value
from .media import MediaDownloader, MediaStore, copy_file_download
from .migration import migrate_legacy
from .models import SearchFilters
from .security import CONFIRMATION_PHRASE, check_path_private, harden_path, is_automation_shell, require_human_confirmation
from .storage import Database
from .telegram_client import TelegramArchiveClient, run_async
from .transcription import SidecarTextProvider, TranscriptionService, WhisperCLIProvider


def main(argv: list[str] | None = None) -> int:
    raw_argv = _normalize_global_arguments(list(sys.argv[1:] if argv is None else argv))
    parser = build_parser()
    args = parser.parse_args(raw_argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 2
    try:
        return args.handler(args)
    except Exception as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": {"code": type(exc).__name__, "message": str(exc)}}))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1


def legacy_main() -> int:
    print("warning: tg-ecosystem is deprecated; use tg-recall", file=sys.stderr)
    return main()


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tg-recall")
    parser.add_argument("--config", help="Legacy config.json path")
    parser.add_argument("--home", help="Portable tg-recall root")
    parser.add_argument("--profile", help="Telegram archive profile")
    parser.add_argument("--json", action="store_true", help="Write machine-readable result to stdout")
    parser.add_argument("--confirm-risk", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command")

    setup = sub.add_parser("setup", help="Create local profile config and database")
    setup.set_defaults(handler=cmd_setup)

    doctor = sub.add_parser("doctor", help="Inspect local archive, providers and file protection")
    doctor.set_defaults(handler=cmd_doctor)

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
    _add_scope_arguments(scopes_create, include_name=True)
    scopes_create.set_defaults(handler=cmd_scope_create)
    scopes_list = scopes_sub.add_parser("list", help="List sync scopes")
    scopes_list.set_defaults(handler=cmd_scope_list)

    sync = sub.add_parser("sync", help="Run sync jobs")
    sync_sub = sync.add_subparsers(dest="sync_command")
    sync_run = sync_sub.add_parser("run", help="Sync messages for an existing scope")
    sync_run.add_argument("scope")
    sync_run.add_argument("--limit", type=int, default=100)
    sync_run.add_argument("--backfill", action="store_true", help="Fetch messages older than the stored watermark")
    sync_run.set_defaults(handler=cmd_sync_run)
    sync_ensure = sync_sub.add_parser("ensure", help="Create/update a scope and process its queued work")
    _add_scope_arguments(sync_ensure, include_name=True)
    sync_ensure.add_argument("--limit", type=int, default=500)
    sync_ensure.set_defaults(handler=cmd_sync_ensure)

    search = sub.add_parser("search", help="Search indexed archive")
    _add_search_arguments(search, query_required=True)
    search.set_defaults(handler=cmd_search)

    ask = sub.add_parser("ask", help="Retrieve cited evidence for an AI task")
    ask.add_argument("query")
    ask.add_argument("--limit", type=int, default=10)
    ask.add_argument("--chat-id", type=int)
    ask.set_defaults(handler=cmd_ask)

    retrieve = sub.add_parser("retrieve", help="Return bounded cited evidence for an AI task")
    _add_search_arguments(retrieve, query_required=False)
    retrieve.add_argument("--context", type=int, default=3)
    retrieve.add_argument("--token-budget", type=int, default=12000)
    retrieve.set_defaults(handler=cmd_retrieve)

    export = sub.add_parser("export", help="Create a private archive export")
    export.add_argument("--chat", type=int, required=True, dest="chat_id")
    export.add_argument("--since")
    export.add_argument("--until")
    export.add_argument("--include", default="transcripts,media-metadata")
    export.add_argument("--format", choices=["jsonl"], default="jsonl")
    export.add_argument("--output")
    export.add_argument("--limit", type=int, default=100000)
    export.set_defaults(handler=cmd_export)

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
    materialize = media_sub.add_parser("materialize", help="Download media for one cited Telegram message")
    materialize.add_argument("--citation", required=True)
    materialize.set_defaults(handler=cmd_media_materialize)

    transcribe = sub.add_parser("transcribe", help="Run transcription jobs")
    transcribe_sub = transcribe.add_subparsers(dest="transcribe_command")
    transcribe_run = transcribe_sub.add_parser("run", help="Run pending transcription jobs")
    transcribe_run.add_argument("--limit", type=int, default=20)
    transcribe_run.add_argument("--provider", choices=["sidecar", "telegram", "local", "auto"], default="sidecar")
    transcribe_run.add_argument("--telegram", action="store_true", help="Deprecated alias for --provider telegram")
    transcribe_run.set_defaults(handler=cmd_transcribe_run)

    index = sub.add_parser("index", help="Index maintenance")
    index_sub = index.add_subparsers(dest="index_command")
    index_rebuild = index_sub.add_parser("rebuild", help="Rebuild keyword and semantic indexes")
    index_rebuild.set_defaults(handler=cmd_index_rebuild)

    backup = sub.add_parser("backup", help="Create or restore a consistent local backup")
    backup_sub = backup.add_subparsers(dest="backup_command")
    backup_create = backup_sub.add_parser("create", help="Create a backup ZIP")
    backup_create.add_argument("--mode", choices=["essential", "full"], default="essential")
    backup_create.add_argument("--include-session", action="store_true")
    backup_create.add_argument("--output", required=True)
    backup_create.set_defaults(handler=cmd_backup_create)
    backup_restore = backup_sub.add_parser("restore", help="Restore a backup into a profile")
    backup_restore.add_argument("archive")
    backup_restore.add_argument("--replace", action="store_true")
    backup_restore.set_defaults(handler=cmd_backup_restore)

    migrate = sub.add_parser("migrate", help="Migrate legacy tg-ecosystem state")
    migrate_sub = migrate.add_subparsers(dest="migrate_command")
    migrate_legacy_parser = migrate_sub.add_parser("legacy", help="Copy a legacy .tg-ecosystem archive into a profile")
    migrate_legacy_parser.add_argument("--from", required=True, dest="source")
    migrate_legacy_parser.add_argument("--dry-run", action="store_true")
    migrate_legacy_parser.set_defaults(handler=cmd_migrate_legacy)

    purge = sub.add_parser("purge", help="Delete local archive data")
    purge_group = purge.add_mutually_exclusive_group(required=True)
    purge_group.add_argument("--chat-id", type=int)
    purge_group.add_argument("--all", action="store_true")
    purge.set_defaults(handler=cmd_purge)

    agent = sub.add_parser("agent", help="Self-describing safe workflow for local AI agents")
    agent_sub = agent.add_subparsers(dest="agent_command")
    agent_guide = agent_sub.add_parser("guide", help="Show current agent workflow")
    agent_guide.set_defaults(handler=cmd_agent_guide)
    return parser


def _add_scope_arguments(parser: argparse.ArgumentParser, *, include_name: bool) -> None:
    if include_name:
        parser.add_argument("name")
    parser.add_argument("--chat", type=int, action="append", required=True, dest="chats")
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--media", default="none")
    parser.add_argument("--transcribe", choices=["off", "telegram", "local", "auto"], default="off")


def _add_search_arguments(parser: argparse.ArgumentParser, *, query_required: bool) -> None:
    parser.add_argument("query", nargs=None if query_required else "?", default=None)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--chat-id", type=int, required=not query_required)
    parser.add_argument("--sender-id", type=int)
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--media-type")
    parser.add_argument("--has-link", action="store_true")
    parser.add_argument("--semantic", action="store_true")


def services(args: argparse.Namespace) -> tuple[AppConfig, Database]:
    cfg = load_config(args.config, home=args.home, profile=args.profile)
    cfg.ensure_dirs()
    db = Database(cfg.db_path)
    db.migrate()
    return cfg, db


def emit(args: argparse.Namespace, value: Any, plain: str | None = None) -> int:
    if args.json:
        print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
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


def cmd_chats_list(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    if args.cached:
        chats = [dict(row) for row in db.list_chats()]
    else:
        require_human_confirmation("live Telegram chat discovery", args.confirm_risk)
        chats = [chat.__dict__ for chat in run_async(TelegramArchiveClient(cfg, db).discover_chats(limit=args.limit))]
    if args.json:
        return emit(args, chats)
    for chat in chats:
        print(f"{chat['chat_id']}\t{chat['chat_type']}\t{chat['title']}")
    return 0


def cmd_scope_create(args: argparse.Namespace) -> int:
    require_human_confirmation("scope create", args.confirm_risk)
    _, db = services(args)
    db.create_scope(args.name, args.chats, args.since, args.until, args.media, args.transcribe)
    db.audit("scope_saved", args.name, chats=args.chats, since=args.since, media=args.media, transcribe=args.transcribe)
    return emit(args, {"scope": args.name, "saved": True})


def cmd_scope_list(args: argparse.Namespace) -> int:
    _, db = services(args)
    scopes = db.list_scopes()
    if args.json:
        return emit(args, scopes)
    for scope in scopes:
        print(json.dumps(scope, ensure_ascii=False))
    return 0


def cmd_sync_run(args: argparse.Namespace) -> int:
    require_human_confirmation("sync run", args.confirm_risk)
    cfg, db = services(args)
    return emit(args, run_async(TelegramArchiveClient(cfg, db).sync_scope(args.scope, limit=args.limit, backfill=args.backfill)))


def cmd_sync_ensure(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    db.create_scope(args.name, args.chats, args.since, args.until, args.media, args.transcribe)
    client = TelegramArchiveClient(cfg, db)
    result: dict[str, Any] = {"scope": args.name}
    result["sync"] = run_async(client.sync_scope(args.name, limit=args.limit))
    pending_media_ids = db.pending_media_ids_for_chats(args.chats, args.media)
    result["media"] = (
        run_async(client.download_pending_media(limit=min(args.limit, len(pending_media_ids)), media_ids=pending_media_ids))
        if args.media != "none" and pending_media_ids
        else {"skipped": True}
    )
    scope_media_ids = db.media_ids_for_chats(args.chats, args.media)
    result["transcription"] = _run_transcription_policy(args, cfg, db, client, args.transcribe, args.limit, scope_media_ids)
    return emit(args, result)


def cmd_search(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    filters = _filters_from_args(args)
    results = db.semantic_search(args.query, limit=args.limit, filters=filters) if args.semantic else db.search(args.query, limit=args.limit, filters=filters)
    data = [_result_dict(item) for item in results]
    if args.json:
        return emit(args, data)
    for item in results:
        print(f"{item.timestamp}\t{item.chat_title}\t{item.citation}\t{item.text}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    answer = ArchiveAssistant(db, cfg).answer(args.query, limit=args.limit, chat_id=args.chat_id)
    return emit(args, {"answer": answer} if args.json else answer)


def cmd_retrieve(args: argparse.Namespace) -> int:
    _, db = services(args)
    filters = _filters_from_args(args)
    if args.query:
        hits = db.semantic_search(args.query, limit=args.limit, filters=filters) if args.semantic else db.search(args.query, limit=args.limit, filters=filters)
    else:
        hits = [
            _search_result_from_export(item)
            for item in db.export_messages(filters, limit=args.limit)
        ]
    seen: set[tuple[int, int]] = set()
    items: list[dict[str, Any]] = []
    chars_left = max(args.token_budget, 1) * 4
    for hit in hits:
        for context in db.message_context(hit.chat_id, hit.message_id, radius=max(args.context, 0)):
            key = (context.chat_id, context.message_id)
            if key in seen:
                continue
            payload = _result_dict(context)
            cost = len(payload["text"]) + 100
            if items and cost > chars_left:
                break
            seen.add(key)
            items.append(payload)
            chars_left -= cost
        if chars_left <= 0:
            break
    return emit(args, {"chat_id": args.chat_id, "items": items, "truncated": chars_left <= 0})


def cmd_export(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    filters = SearchFilters(chat_id=args.chat_id, since=args.since, until=args.until)
    items = db.export_messages(filters, limit=args.limit)
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
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    harden_path(target, is_dir=False)
    return emit(args, {"path": str(target), "messages": len(items), "format": args.format})


def cmd_jobs(args: argparse.Namespace) -> int:
    _, db = services(args)
    with db.connect() as conn:
        jobs = [dict(row) for row in conn.execute("SELECT id, stage, status, chat_id, message_id, media_id, error, retry_after, updated_at FROM jobs ORDER BY updated_at DESC LIMIT 50")]
    return emit(args, jobs)


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
    result = _run_transcription_policy(args, cfg, db, TelegramArchiveClient(cfg, db), policy, args.limit)
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


def cmd_migrate_legacy(args: argparse.Namespace) -> int:
    profile = args.profile or "default"
    result = migrate_legacy(args.source, home=args.home, profile=profile, dry_run=args.dry_run)
    return emit(args, result)


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
    cfg, db = services(args)
    status: dict[str, Any] = {
        "profile": cfg.profile,
        "archive": db.health(),
        "providers": {"ffmpeg": shutil.which("ffmpeg") is not None, "whisper": WhisperCLIProvider.available()},
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
    return emit(args, status)


def cmd_agent_guide(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    text = (
        "For Telegram tasks, use tg-recall only for chats explicitly requested by the user. "
        "Run `tg-recall doctor --json` when freshness or providers are unknown. "
        "Use `tg-recall sync ensure SCOPE --chat CHAT_ID --media voice,photo --transcribe auto --json` to refresh data, "
        "then `tg-recall retrieve --chat-id CHAT_ID --query QUERY --json` for cited evidence. "
        "For long analysis, create `tg-recall export --chat CHAT_ID --format jsonl`. "
        "Do not run telegram auth, config set, or purge. Cite conclusions with tg:// links."
    )
    return emit(args, {"profile": cfg.profile, "health": db.health(), "guide": text}, plain=text)


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
    provider = WhisperCLIProvider(Path(cfg.cache_dir) / "transcription")
    return TranscriptionService(db, fallback_provider=provider, media_store=store, cache_dir=Path(cfg.cache_dir) / "extracted-audio").run_pending(limit=limit, media_ids=media_ids)


def _filters_from_args(args: argparse.Namespace) -> SearchFilters:
    return SearchFilters(
        chat_id=args.chat_id,
        sender_id=getattr(args, "sender_id", None),
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
        media_type=getattr(args, "media_type", None),
        has_link=True if getattr(args, "has_link", False) else None,
    )


def _result_dict(item: Any) -> dict[str, Any]:
    return {
        "chat_id": item.chat_id, "chat_title": item.chat_title, "message_id": item.message_id,
        "timestamp": item.timestamp, "text": item.text, "media_id": item.media_id,
        "transcript_id": item.transcript_id, "citation": item.citation,
    }


def _search_result_from_export(item: dict[str, Any]) -> Any:
    from .models import SearchResult
    return SearchResult(item["chat_id"], item["chat_title"] or "", item["message_id"], item["date"], item["text"])


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
