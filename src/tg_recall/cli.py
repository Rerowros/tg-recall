from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .agent_routing import HarnessTarget, build_agent_guide
from .assistant import ArchiveAssistant, expand_cited_sources, knowledge_catalog_lookup, local_embedding_provider
from .backup import create_backup, restore_backup
from .config import AppConfig, load_config, redact_config, save_config, set_config_value
from .export_packs import ExportScope, PackError, WikiPage, build_pack, inspect_pack, verify_pack
from .media import MediaDownloader, MediaStore, copy_file_download
from .migration import migrate_legacy
from .models import SearchFilters
from .hybrid_retrieval import RetrievalMode, SemanticUnavailableError
from .harness_integrations import (
    ALL_HARNESSES,
    IntegrationAction,
    IntegrationLocations,
    harness_capabilities,
    run_harness_lifecycle,
)
from .integration_files import IntegrationFileError, IntegrationScope, IntegrationStatus, ensure_integration_directory
from .knowledge_catalog import KnowledgeScope, ResearchCheckpoint, ResearchSession, VersionMap, plan_evidence_reuse
from .lifecycle_settings import UpdateCheckSettings, UpdateSettingsStore
from .security import (
    AgentOperation,
    AgentPolicyError,
    PolicyDecision,
    RequestedAgentScope,
    audit_policy_decision,
    check_path_private,
    harden_path,
    is_automation_shell,
    require_agent_policy,
    require_human_confirmation,
)
from .paths import AppRoots
from .release_updates import (
    UPDATE_SCHEMA_VERSION,
    GitHubReleaseChecker,
    ReleaseCache,
    ReleaseTransportError,
    ReleaseUpdateError,
    apply_verified_wheel,
    detect_installation_provenance,
    stage_verified_wheel,
)
from .storage import Database, SchemaCompatibilityError
from .telegram_client import TelegramArchiveClient, run_async
from .transcription import TranscriptionService, WhisperCLIProvider
from .wiki_memory import AuthorizedWikiScope, WikiMemoryStore
from .versioning import runtime_package_version


def main(argv: list[str] | None = None) -> int:
    raw_argv = _normalize_global_arguments(list(sys.argv[1:] if argv is None else argv))
    parser = build_parser()
    args = parser.parse_args(raw_argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 2
    try:
        if _is_lifecycle_command(args):
            _enforce_lifecycle_human()
            args._agent_policy = None
        else:
            args._agent_policy = _enforce_agent_command(args)
        exit_code = args.handler(args)
        _maybe_emit_periodic_update_notice(args, exit_code)
        return exit_code
    except AgentPolicyError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": {"code": exc.error_code, "message": str(exc), "details": exc.details}}))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
    except SemanticUnavailableError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": {"code": exc.code, "message": str(exc), "reason": exc.reason}}))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
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

    update = sub.add_parser("update", help="Human-only tg-recall release updates")
    update_sub = update.add_subparsers(dest="update_command", required=True)
    update_check = update_sub.add_parser("check", help="Explicitly check the fixed GitHub Release endpoint")
    update_check.add_argument("--refresh", action="store_true", help="Ignore a fresh cached release result")
    update_check.add_argument("--offline", action="store_true", help="Use cached public release metadata only")
    update_check.set_defaults(handler=cmd_update_check)
    update_status = update_sub.add_parser("status", help="Inspect cached release and installation provenance without network access")
    update_status.set_defaults(handler=cmd_update_status)
    update_configure = update_sub.add_parser("configure", help="Opt in to or disable periodic interactive update notices")
    configure_group = update_configure.add_mutually_exclusive_group(required=True)
    configure_group.add_argument("--interval-hours", type=int, help="Enable periodic checks at this interval (1..720 hours)")
    configure_group.add_argument("--disable", action="store_true", help="Disable periodic update checks")
    update_configure.set_defaults(handler=cmd_update_configure)
    update_apply = update_sub.add_parser("apply", help="Apply a verified release wheel for supported uv tool installs")
    update_apply.add_argument("--refresh", action="store_true", help="Refresh release metadata before applying")
    update_apply.set_defaults(handler=cmd_update_apply)

    integrate = sub.add_parser("integrate", help="Human-only AI harness integration lifecycle")
    integrate_sub = integrate.add_subparsers(dest="integration_command", required=True)
    integrate_list = integrate_sub.add_parser("list", help="List documented harness capabilities")
    integrate_list.set_defaults(handler=cmd_integrate_list)
    for action in IntegrationAction:
        command = integrate_sub.add_parser(action.value, help=f"{action.value.capitalize()} explicit harness integration files")
        command.add_argument("--target", action="append", required=True, choices=[*HarnessTarget, ALL_HARNESSES])
        command.add_argument("--scope", required=True, choices=[scope.value for scope in IntegrationScope])
        command.add_argument("--project-root", help="Required explicit project root for --scope project")
        command.add_argument("--generic-output-root", help="Explicit output root required for target generic or all")
        command.add_argument("--generic-instructions", help="Explicit generic instruction destination below --generic-output-root")
        command.add_argument("--generic-mcp", help="Explicit generic MCP destination below --generic-output-root")
        command.set_defaults(handler=cmd_integrate)

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
    ask.add_argument("--since")
    ask.add_argument("--until")
    ask.add_argument("--media-type")
    ask.add_argument("--token-budget", type=int, default=12_000, help="Maximum serialized external-provider context")
    ask.set_defaults(handler=cmd_ask)

    retrieve = sub.add_parser("retrieve", help="Return bounded cited evidence for an AI task")
    _add_search_arguments(retrieve, query_required=False)
    retrieve.add_argument("--context", type=int, default=3)
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

    pack = sub.add_parser("pack", help="Create and verify bounded private AI export packs")
    pack_sub = pack.add_subparsers(dest="pack_command")
    pack_create = pack_sub.add_parser("create", help="Create a bounded raw/wiki/mixed private pack")
    pack_create.add_argument("name", help="Normalized pack name")
    pack_create.add_argument("--chat", type=int, action="append", dest="chats")
    pack_create.add_argument("--scope", help="Saved sync scope; alternative to --chat with a date boundary")
    pack_create.add_argument("--since")
    pack_create.add_argument("--until")
    pack_create.add_argument("--max-records", type=int, required=True)
    pack_create.add_argument("--token-budget", type=int, required=True)
    pack_create.add_argument("--wiki-scope", help="Saved wiki scope containing the selected revisions")
    pack_create.add_argument("--wiki-revision", action="append", default=[], help="Explicit immutable wiki revision ID")
    pack_create.add_argument("--output", help="Explicit human-only external destination")
    pack_create.add_argument("--previous", help="Verified prior pack to reuse unchanged generated files")
    pack_create.set_defaults(handler=cmd_pack_create)
    pack_inspect = pack_sub.add_parser("inspect", help="Inspect a local pack manifest offline")
    pack_inspect.add_argument("path")
    pack_inspect.set_defaults(handler=cmd_pack_inspect)
    pack_verify = pack_sub.add_parser("verify", help="Verify a local pack offline")
    pack_verify.add_argument("path")
    pack_verify.add_argument("--non-strict", action="store_true", help="Allow undeclared extra files")
    pack_verify.add_argument("--max-wiki-age-seconds", type=int)
    pack_verify.set_defaults(handler=cmd_pack_verify)

    knowledge = sub.add_parser("knowledge", help="Profile-local compact knowledge catalog")
    knowledge_sub = knowledge.add_subparsers(dest="knowledge_command")
    knowledge_query = knowledge_sub.add_parser("query", help="Query compact cited catalog metadata")
    knowledge_query.add_argument("query")
    knowledge_query.add_argument("--scope", required=True, help="Exact saved scope owning catalog metadata")
    knowledge_query.add_argument("--limit", type=int, default=8)
    knowledge_query.set_defaults(handler=cmd_knowledge_query)

    research = sub.add_parser("research-session", help="Compact profile-local research session workflow")
    research_sub = research.add_subparsers(dest="research_command")
    research_create = research_sub.add_parser("create", help="Create a compact session in one saved scope")
    research_create.add_argument("session_id")
    research_create.add_argument("--scope", required=True)
    research_create.add_argument("--purpose", required=True)
    _add_checkpoint_arguments(research_create, initial=True)
    _add_research_budget_arguments(research_create)
    research_create.set_defaults(handler=cmd_research_create)
    research_checkpoint = research_sub.add_parser("checkpoint", help="Append a compact checkpoint")
    research_checkpoint.add_argument("session_id")
    _add_checkpoint_arguments(research_checkpoint, initial=False)
    research_checkpoint.set_defaults(handler=cmd_research_checkpoint)
    research_list = research_sub.add_parser("list", help="List compact sessions in this profile")
    research_list.add_argument("--scope")
    research_list.add_argument("--limit", type=int, default=50)
    research_list.set_defaults(handler=cmd_research_list)
    for action, handler, help_text in (
        ("inspect", cmd_research_inspect, "Inspect compact session metadata"),
        ("resume", cmd_research_resume, "Return latest checkpoint plus current/stale evidence plans"),
        ("selective-refresh", cmd_research_selective_refresh, "Plan refresh for only stale referenced sources"),
    ):
        command = research_sub.add_parser(action, help=help_text)
        command.add_argument("session_id")
        command.set_defaults(handler=handler)
    research_expand = research_sub.add_parser("expand", help="Expand only explicit cited raw sources")
    research_expand.add_argument("session_id")
    research_expand.add_argument("--citation", action="append", required=True)
    research_expand.add_argument("--limit", type=int, default=8)
    research_expand.add_argument("--context", type=int, default=2)
    research_expand.add_argument("--token-budget", type=int, default=4000)
    research_expand.set_defaults(handler=cmd_research_expand)

    jobs = sub.add_parser("jobs", help="Inspect jobs")
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
    transcribe_run.add_argument("--citation", help="Transcribe media for one cited Telegram message")
    transcribe_run.set_defaults(handler=cmd_transcribe_run)

    index = sub.add_parser("index", help="Index maintenance")
    index_sub = index.add_subparsers(dest="index_command")
    index_rebuild = index_sub.add_parser("rebuild", help="Rebuild keyword and semantic indexes")
    index_rebuild.set_defaults(handler=cmd_index_rebuild)
    embeddings = index_sub.add_parser("embeddings", help="Explicit local embedding-index maintenance")
    embeddings_sub = embeddings.add_subparsers(dest="embedding_command")
    embedding_build = embeddings_sub.add_parser("build", help="Build bounded batches from an already-local model")
    _add_embedding_scope_arguments(embedding_build)
    embedding_build.add_argument("--batch-size", type=int)
    embedding_build.add_argument("--max-batches", type=int, default=1, help="Completed local batches per run (default: 1)")
    embedding_build.set_defaults(handler=cmd_embedding_build)
    embedding_status = embeddings_sub.add_parser("status", help="Show local embedding index freshness")
    _add_embedding_scope_arguments(embedding_status)
    embedding_status.set_defaults(handler=cmd_embedding_status)
    embedding_rebuild = embeddings_sub.add_parser("rebuild", help="Explicitly recompute selected local vectors")
    _add_embedding_scope_arguments(embedding_rebuild)
    embedding_rebuild.add_argument("--batch-size", type=int)
    embedding_rebuild.add_argument("--max-batches", type=int, default=1)
    embedding_rebuild.set_defaults(handler=cmd_embedding_rebuild)
    embedding_remove = embeddings_sub.add_parser("remove", help="Remove vectors only; never archive messages")
    removal = embedding_remove.add_mutually_exclusive_group(required=True)
    removal.add_argument("--all", action="store_true", dest="all_models")
    removal.add_argument("--model-identity")
    embedding_remove.set_defaults(handler=cmd_embedding_remove)

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
    parser.add_argument("--retrieval-mode", choices=[mode.value for mode in RetrievalMode], help="Use explicit keyword, auto, hybrid, or strict semantic vector retrieval")
    parser.add_argument("--token-budget", type=int, default=12000, help="Total evidence-window token budget for --retrieval-mode")


def _add_embedding_scope_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--chat-id", type=int, required=True, help="Explicit selected chat; no whole-profile embedding build")
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--media-type")


def _add_checkpoint_arguments(parser: argparse.ArgumentParser, *, initial: bool) -> None:
    parser.add_argument("--summary", required=True)
    parser.add_argument("--decision", action="append", default=[])
    parser.add_argument("--unresolved", action="append", default=[])
    parser.add_argument("--evidence-set", action="append", default=[])
    parser.add_argument("--at", help="Explicit ISO-8601 checkpoint timestamp")


def _add_research_budget_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--item-limit", type=int, default=8)
    parser.add_argument("--context-radius", type=int, default=2)
    parser.add_argument("--token-budget", type=int, default=4000)
    parser.add_argument("--stage-budget", type=int, default=3)
    parser.add_argument("--retry-budget", type=int, default=1)
    parser.add_argument("--tool-call-budget", type=int, default=2)


def services(args: argparse.Namespace) -> tuple[AppConfig, Database]:
    cfg = load_config(args.config, home=args.home, profile=args.profile)
    cfg.ensure_dirs()
    db = Database(cfg.db_path)
    db.migrate()
    return cfg, db


def _is_lifecycle_command(args: argparse.Namespace) -> bool:
    """Keep future integration handlers behind the same pre-config boundary."""

    return args.command in {"update", "integrate"}


def _enforce_lifecycle_human() -> None:
    """Reject lifecycle namespaces before config, harness, network, or SQLite I/O."""

    if is_automation_shell():
        raise AgentPolicyError(
            PolicyDecision(
                operation=AgentOperation.HUMAN_ONLY,
                allowed=False,
                error_code="agent_operation_forbidden",
                message="update and integration lifecycle commands are not available to automation",
                details={"operation": "human_only"},
            )
        )


def _maybe_emit_periodic_update_notice(args: argparse.Namespace, exit_code: int) -> None:
    """Best-effort, opt-in notice; it never changes the requested command result."""

    if exit_code != 0 or args.json or _is_lifecycle_command(args) or is_automation_shell():
        return
    try:
        roots = _app_roots(args)
        settings = UpdateSettingsStore(roots).load()
        if not settings.enabled:
            return
        result = GitHubReleaseChecker(
            ReleaseCache(roots), cache_ttl=timedelta(hours=settings.interval_hours)
        ).check(runtime_package_version())
        if result.status == "update_available" and result.release is not None:
            print(f"notice: tg-recall {result.release.version} is available; run `tg-recall update check`.", file=sys.stderr)
    except Exception:
        return


def _enforce_agent_command(args: argparse.Namespace):
    """Apply the one operation table before a handler opens Telegram or writes data."""

    operation, requested = _agent_operation_and_scope(args)
    cfg = load_config(args.config, home=args.home, profile=args.profile)
    policy = cfg.ai_access
    try:
        decision = require_agent_policy(
            operation,
            enabled=policy.enabled,
            allowed_chat_ids=policy.allowed_chat_ids,
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


def _agent_operation_and_scope(args: argparse.Namespace) -> tuple[AgentOperation, RequestedAgentScope]:
    command = args.command
    if command == "agent":
        return AgentOperation.AGENT_GUIDE, RequestedAgentScope()
    if command in {"config", "setup", "purge", "backup", "migrate", "index", "update", "integrate"}:
        return AgentOperation.HUMAN_ONLY, RequestedAgentScope()
    if command == "telegram":
        return AgentOperation.HUMAN_ONLY, RequestedAgentScope()
    if command == "security":
        return AgentOperation.HUMAN_ONLY, RequestedAgentScope()
    if command == "chats":
        return (AgentOperation.METADATA_LIST if args.cached else AgentOperation.HUMAN_ONLY), RequestedAgentScope()
    if command == "scopes":
        return (AgentOperation.METADATA_LIST if args.scopes_command == "list" else AgentOperation.HUMAN_ONLY), RequestedAgentScope()
    if command in {"jobs"} or (command == "media" and args.media_command in {"usage", "download"}):
        return AgentOperation.HUMAN_ONLY, RequestedAgentScope()
    if command == "doctor":
        return AgentOperation.METADATA_LIST, RequestedAgentScope()
    if command == "pack":
        if args.pack_command == "create":
            saved_scope = _saved_scope_for_agent(args) if args.scope else None
            return (
                AgentOperation.ARCHIVE_EXPORT,
                RequestedAgentScope(
                    chat_ids=tuple(args.chats or ()),
                    since=args.since,
                    until=args.until,
                    result_limit=args.max_records,
                    saved_scope=saved_scope,
                ),
            )
        return AgentOperation.METADATA_LIST, RequestedAgentScope()
    if command == "knowledge":
        saved_scope = _saved_scope_for_agent(args)
        return (
            AgentOperation.ARCHIVE_READ,
            RequestedAgentScope(
                chat_ids=tuple(saved_scope.get("chat_ids", ()) if saved_scope else ()),
                saved_scope=saved_scope,
                result_limit=args.limit,
            ),
        )
    if command == "research-session":
        if args.research_command in {"create", "checkpoint", "list", "selective-refresh"}:
            return AgentOperation.HUMAN_ONLY, RequestedAgentScope()
        session_scope = _research_session_scope_for_agent(args)
        return (
            AgentOperation.ARCHIVE_READ,
            RequestedAgentScope(
                chat_ids=tuple(session_scope.get("chat_ids", ()) if session_scope else ()),
                result_limit=getattr(args, "limit", None),
                saved_scope=session_scope,
            ),
        )
    if command in {"search", "ask", "retrieve", "export"}:
        chat_id = getattr(args, "chat_id", None)
        return (
            AgentOperation.ARCHIVE_EXPORT if command == "export" else AgentOperation.ARCHIVE_READ,
            RequestedAgentScope(
                chat_ids=(chat_id,) if chat_id is not None else (),
                since=getattr(args, "since", None),
                until=getattr(args, "until", None),
                media_policy=getattr(args, "media_type", None),
                result_limit=getattr(args, "limit", None),
            ),
        )
    if command == "sync":
        saved_scope = _saved_scope_for_agent(args) if args.sync_command == "run" else None
        return (
            AgentOperation.SYNC,
            RequestedAgentScope(
                chat_ids=tuple(getattr(args, "chats", ()) or ()),
                since=getattr(args, "since", None),
                until=getattr(args, "until", None),
                media_policy=getattr(args, "media", None),
                saved_scope=saved_scope,
            ),
        )
    if command == "media" and args.media_command == "materialize":
        try:
            chat_id, _ = _parse_citation(args.citation)
        except ValueError:
            chat_id = None
        return AgentOperation.MEDIA_MATERIALIZE, RequestedAgentScope(chat_ids=(chat_id,) if chat_id is not None else ())
    if command == "transcribe" and args.citation:
        try:
            chat_id, _ = _parse_citation(args.citation)
        except ValueError:
            chat_id = None
        return AgentOperation.TRANSCRIBE, RequestedAgentScope(chat_ids=(chat_id,) if chat_id is not None else ())
    return AgentOperation.HUMAN_ONLY, RequestedAgentScope()


def _saved_scope_for_agent(args: argparse.Namespace) -> dict[str, Any] | None:
    cfg = load_config(args.config, home=args.home, profile=args.profile)
    db_path = Path(cfg.db_path)
    if not db_path.exists():
        return None
    return Database(db_path).get_scope(args.scope)


def _research_session_scope_for_agent(args: argparse.Namespace) -> dict[str, Any] | None:
    cfg = load_config(args.config, home=args.home, profile=args.profile)
    db_path = Path(cfg.db_path)
    if not db_path.exists():
        return None
    try:
        view = Database(db_path).research_session_view(profile_id=cfg.profile, session_id=args.session_id)
    except KeyError:
        return None
    scope = view["scope"]
    return {"chat_ids": scope["chat_ids"], "name": scope["scope_id"]}


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
        print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    elif plain is not None:
        print(plain)
    elif isinstance(value, str):
        print(value)
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_update_check(args: argparse.Namespace) -> int:
    cache = _release_cache(args)
    result = GitHubReleaseChecker(cache).check(runtime_package_version(), refresh=args.refresh, offline=args.offline)
    payload = _update_payload("check", result.status, check=result)
    plain = _update_plain_summary(payload)
    return _emit_update(args, payload, plain=plain, exit_code=0 if result.status in {"current", "update_available"} else 1)


def cmd_update_status(args: argparse.Namespace) -> int:
    cache = _release_cache(args)
    result = GitHubReleaseChecker(cache).check(runtime_package_version(), offline=True)
    provenance = detect_installation_provenance(release_cache=cache)
    settings = UpdateSettingsStore(_app_roots(args)).load()
    payload = _update_payload("status", result.status, check=result, provenance=provenance, settings=settings)
    return _emit_update(args, payload, plain=_update_plain_summary(payload), exit_code=0)


def cmd_update_configure(args: argparse.Namespace) -> int:
    store = UpdateSettingsStore(_app_roots(args))
    current = store.load()
    settings = UpdateCheckSettings(enabled=False, interval_hours=current.interval_hours) if args.disable else UpdateCheckSettings(enabled=True, interval_hours=args.interval_hours)
    store.save(settings)
    status = "disabled" if args.disable else "configured"
    payload = _update_payload("configure", status, settings=settings)
    return _emit_update(args, payload, plain=_update_plain_summary(payload))


def cmd_update_apply(args: argparse.Namespace) -> int:
    cache = _release_cache(args)
    check = GitHubReleaseChecker(cache).check(runtime_package_version(), refresh=args.refresh)
    provenance = detect_installation_provenance(release_cache=cache)
    if check.status != "update_available" or check.release is None:
        payload = _update_payload(
            "apply",
            check.status,
            check=check,
            provenance=provenance,
            warnings=("No newer verified release is available to apply.",),
        )
        return _emit_update(args, payload, plain=_update_plain_summary(payload), exit_code=0 if check.status == "current" else 1)
    if not provenance.supported_for_apply:
        payload = _update_payload(
            "apply",
            "manual_required",
            check=check,
            provenance=provenance,
            warnings=("This installation source cannot be updated in place.",),
            next_actions=provenance.manual_action,
        )
        return _emit_update(args, payload, plain=_update_plain_summary(payload), exit_code=1)
    try:
        artifact = stage_verified_wheel(cache, check.release, _download_release_wheel)
    except (ReleaseTransportError, ReleaseUpdateError, OSError) as exc:
        payload = _update_payload(
            "apply",
            "verification_failed",
            check=check,
            provenance=provenance,
            warnings=(f"Verified wheel staging failed: {type(exc).__name__}.",),
            error_code="wheel_staging_failed",
        )
        return _emit_update(args, payload, plain=_update_plain_summary(payload), exit_code=1)
    applied = apply_verified_wheel(provenance, check.release, artifact, release_cache=cache)
    payload = _update_payload(
        "apply",
        applied.status,
        check=check,
        provenance=provenance,
        warnings=((f"Update error: {applied.error_code}.",) if applied.error_code else ()),
        next_actions=(("Run tg-recall integrate refresh after a successful update.",) if applied.status == "applied" else applied.manual_action),
        error_code=applied.error_code,
    )
    return _emit_update(args, payload, plain=_update_plain_summary(payload), exit_code=0 if applied.status == "applied" else 1)


def _app_roots(args: argparse.Namespace) -> AppRoots:
    return AppRoots.resolve(args.home)


def _release_cache(args: argparse.Namespace) -> ReleaseCache:
    return ReleaseCache(_app_roots(args))


def _update_payload(
    action: str,
    status: str,
    *,
    check: Any | None = None,
    provenance: Any | None = None,
    settings: UpdateCheckSettings | None = None,
    warnings: tuple[str, ...] = (),
    next_actions: tuple[str, ...] = (),
    error_code: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": UPDATE_SCHEMA_VERSION,
        "action": action,
        "status": status,
        "installed_version": runtime_package_version(),
        "release": check.release.as_json() if check and check.release else None,
        "provenance": provenance.as_json() if provenance else None,
        "update_checks": settings.as_json() if settings else None,
        "cache_status": check.cache_status if check else None,
        "network_attempted": check.network_attempted if check else False,
        "error_code": error_code or (check.error_code if check else None),
        "changed_paths": [],
        "warnings": list(warnings),
        "next_actions": list(next_actions),
    }


def _update_plain_summary(payload: dict[str, Any]) -> str:
    action = payload["action"]
    status = payload["status"]
    release = payload["release"]
    version = release["version"] if release else None
    detail = f" latest={version}" if version else ""
    return f"Update {action}: {status}.{detail}"


def _emit_update(args: argparse.Namespace, payload: dict[str, Any], *, plain: str, exit_code: int = 0) -> int:
    emit(args, payload, plain=plain)
    return exit_code


class _RejectRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _download_release_wheel(url: str, maximum: int) -> bytes:
    """Fetch the already-validated fixed GitHub wheel URL without redirects."""

    request = Request(url, headers={"User-Agent": "tg-recall-release-update"}, method="GET")
    try:
        with build_opener(_RejectRedirect()).open(request, timeout=10) as response:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > maximum:
                raise ReleaseTransportError("wheel response exceeds the configured size limit")
            payload = response.read(maximum + 1)
            if len(payload) > maximum:
                raise ReleaseTransportError("wheel response exceeds the configured size limit")
            return payload
    except ValueError as exc:
        raise ReleaseTransportError("wheel response has an invalid content length") from exc
    except (HTTPError, URLError, OSError) as exc:
        raise ReleaseTransportError("wheel download failed") from exc


def cmd_integrate_list(args: argparse.Namespace) -> int:
    payload = {
        "schema_version": 1,
        "action": "list",
        "status": "available",
        "installed_version": runtime_package_version(),
        "requested_harnesses": [],
        "scope": None,
        "components": [],
        "harnesses": [item.as_json() for item in harness_capabilities()],
        "changed_paths": [],
        "backup_paths": [],
        "conflicts": [],
        "warnings": [],
        "manual_actions": [],
        "next_actions": [],
    }
    return emit(args, payload, plain="Integration capabilities: codex, claude-code, cursor, generic.")


def cmd_integrate(args: argparse.Namespace) -> int:
    action = IntegrationAction(args.integration_command)
    targets = tuple(args.target)
    scope = IntegrationScope(args.scope)
    try:
        locations = _integration_locations(args, action=action, targets=targets, scope=scope)
        result = run_harness_lifecycle(action, targets, scope, build_agent_guide(), locations=locations).as_json()
    except (IntegrationFileError, OSError, ValueError) as exc:
        result = _integration_error_payload(action, targets, scope, str(exc))
    return emit(
        args,
        result,
        plain=f"Integration {result['action']}: {result['status']}.",
    ) if result["status"] in {IntegrationStatus.INSTALLED.value, IntegrationStatus.UPDATED.value, IntegrationStatus.UNCHANGED.value} else _emit_integration_error(args, result)


def _emit_integration_error(args: argparse.Namespace, payload: dict[str, Any]) -> int:
    emit(args, payload, plain=f"Integration {payload['action']}: {payload['status']}.")
    return 1


def _integration_locations(
    args: argparse.Namespace,
    *,
    action: IntegrationAction,
    targets: tuple[str, ...],
    scope: IntegrationScope,
) -> IntegrationLocations:
    project_root: Path | None = None
    user_home: Path | None = None
    if scope is IntegrationScope.PROJECT:
        if not args.project_root:
            raise ValueError("--project-root is required for --scope project")
        project_root = _lexical_absolute_path(args.project_root)
        if not project_root.is_dir():
            raise ValueError("--project-root must be an existing directory")
    else:
        user_home = _lexical_absolute_path(Path.home())
        if not user_home.is_dir():
            raise ValueError("user home is not an existing directory")

    selected = set(targets)
    # ``all`` deliberately expands to the documented harnesses only. Generic
    # integration always needs explicitly supplied destinations, so require
    # them only when the caller selected it directly.
    generic_selected = HarnessTarget.GENERIC.value in selected
    generic_root: Path | None = None
    generic_instruction_target: Path | None = None
    generic_mcp_target: Path | None = None
    if generic_selected:
        if not args.generic_output_root or not args.generic_instructions or not args.generic_mcp:
            raise ValueError("generic integration requires --generic-output-root, --generic-instructions, and --generic-mcp")
        generic_root = _lexical_absolute_path(args.generic_output_root)
        if not generic_root.is_dir():
            raise ValueError("--generic-output-root must be an existing directory")
        generic_instruction_target = Path(args.generic_instructions)
        generic_mcp_target = Path(args.generic_mcp)

    mutation = action in {IntegrationAction.INSTALL, IntegrationAction.REFRESH, IntegrationAction.UNINSTALL}
    state_root = _integration_state_root(args, create=mutation)
    return IntegrationLocations(
        project_root=project_root,
        user_home=user_home,
        generic_root=generic_root,
        generic_instruction_target=generic_instruction_target,
        generic_mcp_target=generic_mcp_target,
        state_root=state_root,
    )


def _lexical_absolute_path(value: str | Path) -> Path:
    """Normalize ``.``/``..`` without resolving a selected symlink root."""

    return Path(os.path.abspath(os.path.expanduser(os.fspath(value))))


def _integration_state_root(args: argparse.Namespace, *, create: bool) -> Path | None:
    root = _app_roots(args).state
    candidate = root / "integrations" / "v1"
    if not create:
        return candidate if candidate.is_dir() else None
    root.mkdir(parents=True, exist_ok=True)
    privacy = harden_path(root, is_dir=True)
    if privacy.status != "ok":
        raise OSError(f"could not restrict integration state root: {privacy.detail}")
    return ensure_integration_directory(Path("integrations") / "v1", root=root, apply=True).path


def _integration_error_payload(
    action: IntegrationAction,
    targets: tuple[str, ...],
    scope: IntegrationScope,
    detail: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "action": action.value,
        "status": IntegrationStatus.CONFLICT.value,
        "installed_version": runtime_package_version(),
        "requested_harnesses": sorted(targets),
        "scope": scope.value,
        "components": [],
        "changed_paths": [],
        "backup_paths": [],
        "conflicts": [detail],
        "warnings": [],
        "manual_actions": [],
        "next_actions": [],
    }


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
        if is_automation_shell():
            allowed = set(args._agent_policy.chat_ids or cfg.ai_access.allowed_chat_ids)
            chats = [chat for chat in chats if chat["chat_id"] in allowed]
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
    cfg, db = services(args)
    scopes = db.list_scopes()
    if is_automation_shell():
        allowed = set(args._agent_policy.chat_ids or cfg.ai_access.allowed_chat_ids)
        scopes = [{**scope, "chat_ids": [chat_id for chat_id in scope["chat_ids"] if chat_id in allowed]} for scope in scopes]
        scopes = [scope for scope in scopes if scope["chat_ids"]]
    if args.json:
        return emit(args, scopes)
    for scope in scopes:
        print(json.dumps(scope, ensure_ascii=False))
    return 0


def cmd_sync_run(args: argparse.Namespace) -> int:
    if not is_automation_shell():
        require_human_confirmation("sync run", args.confirm_risk)
    cfg, db = services(args)
    scope = db.get_scope(args.scope)
    effective_scope = _effective_sync_scope(scope, args._agent_policy)
    return emit(args, run_async(TelegramArchiveClient(cfg, db).sync_scope(args.scope, limit=args.limit, backfill=args.backfill, effective_scope=effective_scope)))


def cmd_sync_ensure(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    effective = args._agent_policy
    chats = list(effective.chat_ids) if is_automation_shell() else args.chats
    since = effective.since if is_automation_shell() else args.since
    until = effective.until if is_automation_shell() else args.until
    media = effective.media_policy if is_automation_shell() else args.media
    db.create_scope(args.name, chats, since, until, media or "none", args.transcribe)
    client = TelegramArchiveClient(cfg, db)
    result: dict[str, Any] = {"scope": args.name}
    result["sync"] = run_async(client.sync_scope(args.name, limit=args.limit))
    pending_media_ids = db.pending_media_ids_for_chats(chats, media or "none")
    result["media"] = (
        run_async(client.download_pending_media(limit=min(args.limit, len(pending_media_ids)), media_ids=pending_media_ids))
        if media != "none" and pending_media_ids
        else {"skipped": True}
    )
    scope_media_ids = db.media_ids_for_chats(chats, media or "none")
    result["transcription"] = _run_transcription_policy(args, cfg, db, client, args.transcribe, args.limit, scope_media_ids)
    return emit(args, result)


def cmd_search(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    filters = _filters_from_args(args, args._agent_policy)
    limit = args._agent_policy.result_limit if is_automation_shell() else args.limit
    if args.retrieval_mode:
        if args.semantic:
            raise ValueError("--semantic cannot be combined with --retrieval-mode")
        result = ArchiveAssistant(db, cfg).retrieve_hybrid(
            args.query,
            filters=filters,
            limit=limit or args.limit,
            token_budget=args.token_budget,
            mode=RetrievalMode(args.retrieval_mode),
        )
        return emit(args, result.as_json())
    results = db.semantic_search(args.query, limit=limit or args.limit, filters=filters) if args.semantic else db.search(args.query, limit=limit or args.limit, filters=filters)
    data = [_result_dict(item) for item in results]
    if args.json:
        return emit(args, data)
    for item in results:
        print(f"{item.timestamp}\t{item.chat_title}\t{item.citation}\t{item.text}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    policy = args._agent_policy
    limit = policy.result_limit or args.limit if is_automation_shell() else args.limit
    filters = _filters_from_args(args, policy)
    assistant = ArchiveAssistant(db, cfg)
    if cfg.llm.provider == "extractive":
        answer = assistant.answer(args.query, limit=limit, chat_id=filters.chat_id, filters=filters)
        # Keep the v0.2 JSON object exactly stable for local extractive asks.
        return emit(args, {"answer": answer} if args.json else answer)
    if args.token_budget < 1:
        raise ValueError("--token-budget must be positive")
    answer = assistant.answer_with_synthesis(
        args.query,
        limit=limit,
        filters=filters,
        token_budget=args.token_budget,
    )
    payload = answer.as_dict()
    return emit(args, payload if args.json else answer.answer)


def cmd_retrieve(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    filters = _filters_from_args(args, args._agent_policy)
    limit = args._agent_policy.result_limit or args.limit if is_automation_shell() else args.limit
    if args.retrieval_mode:
        if args.semantic:
            raise ValueError("--semantic cannot be combined with --retrieval-mode")
        if not args.query:
            raise ValueError("--retrieval-mode requires a query")
        result = ArchiveAssistant(db, cfg).retrieve_hybrid(
            args.query,
            filters=filters,
            limit=limit,
            token_budget=args.token_budget,
            context_radius=args.context,
            mode=RetrievalMode(args.retrieval_mode),
        )
        return emit(args, {"chat_id": args.chat_id, **result.as_json()})
    if args.query:
        hits = db.semantic_search(args.query, limit=limit, filters=filters) if args.semantic else db.search(args.query, limit=limit, filters=filters)
    else:
        hits = [
            _search_result_from_export(item)
            for item in db.export_messages(filters, limit=limit)
        ]
    seen: set[tuple[int, int]] = set()
    items: list[dict[str, Any]] = []
    chars_left = max(args.token_budget, 1) * 4
    for hit in hits:
        for context in db.message_context(hit.chat_id, hit.message_id, radius=max(args.context, 0), filters=filters):
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


def cmd_pack_create(args: argparse.Namespace) -> int:
    if is_automation_shell() and args.output:
        raise AgentPolicyError(
            _policy_denial(args._agent_policy, "external_pack_path_forbidden", "automation pack creation must use the profile exports directory")
        )
    cfg, db = services(args)
    scope = _pack_scope_from_args(args, db)
    if is_automation_shell():
        scope = _effective_pack_scope(scope, args._agent_policy, args.token_budget)
    raw_evidence = _pack_raw_evidence(db, scope)
    wiki_pages = _pack_wiki_pages(cfg, scope, args.wiki_scope or args.scope, args.wiki_revision)
    previous = _restricted_pack_path(cfg, args.previous, args._agent_policy, label="previous pack") if args.previous else None
    result = build_pack(
        name=args.name,
        scope=scope,
        raw_evidence=raw_evidence,
        wiki_pages=wiki_pages,
        profile_alias=cfg.profile,
        default_exports_dir=cfg.exports_dir,
        output_path=args.output,
        profile_cache_dir=cfg.cache_dir,
        previous_pack=previous,
        generator_version="tg-recall",
    )
    return emit(
        args,
        {
            "operation": "pack_create",
            "path": str(result.path),
            "kind": result.manifest["kind"],
            "files": len(result.manifest["files"]),
            "reused_paths": list(result.reused_paths),
            "schema_version": result.manifest["schema_version"],
        },
    )


def cmd_pack_inspect(args: argparse.Namespace) -> int:
    cfg = load_config(args.config, home=args.home, profile=args.profile)
    path = _restricted_pack_path(cfg, args.path, args._agent_policy, label="pack")
    manifest = inspect_pack(path)
    return emit(
        args,
        {
            "operation": "pack_inspect",
            "kind": manifest["kind"],
            "files": len(manifest["files"]),
            "schema_version": manifest["schema_version"],
            "scope": manifest["scope"],
            "source_snapshot_ids": manifest["source_snapshot_ids"],
        },
    )


def cmd_pack_verify(args: argparse.Namespace) -> int:
    cfg = load_config(args.config, home=args.home, profile=args.profile)
    path = _restricted_pack_path(cfg, args.path, args._agent_policy, label="pack")
    result = verify_pack(path, strict=not args.non_strict, max_wiki_age_seconds=args.max_wiki_age_seconds)
    return emit(args, {"operation": "pack_verify", **result})


def _pack_scope_from_args(args: argparse.Namespace, db: Database) -> ExportScope:
    if args.scope:
        if args.chats:
            raise PackError("--scope cannot be combined with --chat")
        saved = db.get_scope(args.scope)
        if saved is None:
            raise PackError("saved scope was not found")
        return ExportScope(
            chat_ids=tuple(saved["chat_ids"]),
            since=_pack_timestamp(args.since or saved["since"]),
            until=_pack_timestamp(args.until or saved["until"]),
            saved_scope=args.scope,
            max_records=args.max_records,
            token_budget=args.token_budget,
        ).validate()
    return ExportScope(
        chat_ids=tuple(args.chats or ()),
        since=_pack_timestamp(args.since),
        until=_pack_timestamp(args.until),
        max_records=args.max_records,
        token_budget=args.token_budget,
    ).validate()


def _pack_timestamp(value: str | None) -> str | None:
    if value is None or "T" in value:
        return value
    # Saved sync scopes use date-only values.  Export packs require portable
    # timezone-bearing timestamps, so midnight UTC is explicit in the manifest.
    return f"{value}T00:00:00Z"


def _effective_pack_scope(requested: ExportScope, policy: Any, token_budget: int) -> ExportScope:
    """Represent exactly the policy intersection, never the wider saved scope."""

    if policy.result_limit is None:
        raise AgentPolicyError(
            _policy_denial(policy, "invalid_result_limit", "automation pack creation requires a positive bounded result limit")
        )
    requested_since = _pack_timestamp(requested.since)
    requested_until = _pack_timestamp(requested.until)
    effective_since = _pack_timestamp(policy.since)
    effective_until = _pack_timestamp(policy.until)
    # A saved-scope name is meaningful only when all of its material boundary
    # survived the policy intersection.  Otherwise the manifest is direct and
    # carries the narrowed chats/dates explicitly.
    saved_scope = (
        requested.saved_scope
        if requested.saved_scope
        and tuple(sorted(policy.chat_ids)) == tuple(sorted(requested.chat_ids))
        and effective_since == requested_since
        and effective_until == requested_until
        else None
    )
    return ExportScope(
        chat_ids=tuple(policy.chat_ids),
        since=effective_since,
        until=effective_until,
        saved_scope=saved_scope,
        max_records=policy.result_limit,
        token_budget=token_budget,
    ).validate()


def _pack_raw_evidence(db: Database, scope: ExportScope) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for chat_id in sorted(scope.chat_ids):
        records.extend(
            db.export_messages(
                SearchFilters(chat_id=chat_id, since=scope.since, until=scope.until),
                limit=scope.max_records,
            )
        )
    records.sort(key=lambda item: (item["date"], item["chat_id"], item["message_id"]))
    return [{**item, "timestamp": item["date"]} for item in records[: scope.max_records]]


def _pack_wiki_pages(
    cfg: AppConfig,
    scope: ExportScope,
    wiki_scope_id: str | None,
    revision_ids: list[str],
) -> list[WikiPage]:
    if not revision_ids:
        return []
    if not wiki_scope_id:
        raise PackError("--wiki-revision requires --wiki-scope or --scope")
    store = WikiMemoryStore(cfg.wiki_dir)
    wiki_scope = AuthorizedWikiScope(cfg.profile, wiki_scope_id, scope.chat_ids)
    revisions = store.load_revisions(wiki_scope, revision_ids)
    allowed_chat_ids = set(scope.chat_ids)
    for revision in revisions:
        citation_chat_ids = {_wiki_citation_chat_id(citation) for citation in revision.draft.citations}
        if not citation_chat_ids <= allowed_chat_ids:
            raise PackError("selected wiki revision citations exceed the effective pack scope")
    return [
        WikiPage(
            slug=revision.revision_id,
            markdown=store.render_revision(revision),
            assertions=tuple(
                {
                    "assertion_id": assertion.stable_id,
                    "confidence": assertion.confidence,
                    "kind": assertion.kind.value,
                    "source_citations": list(assertion.citations),
                    "text": assertion.text,
                }
                for assertion in revision.draft.assertions
            ),
            snapshot_id=revision.snapshot_id,
            updated_at=revision.updated_at,
        )
        for revision in revisions
    ]


def _wiki_citation_chat_id(citation: str) -> int:
    parts = citation.split("/")
    # Exact wiki citations are tg://chat/{integer}/message/{integer}.
    if len(parts) != 6 or parts[:3] != ["tg:", "", "chat"] or parts[4] != "message":
        raise PackError("wiki assertion citation is invalid")
    try:
        chat_id = int(parts[3])
        int(parts[5])
        return chat_id
    except ValueError as exc:
        raise PackError("wiki assertion citation is invalid") from exc


def _restricted_pack_path(cfg: AppConfig, value: str, policy: Any, *, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not is_automation_shell():
        return path
    exports_root = Path(cfg.exports_dir).resolve()
    try:
        path.relative_to(exports_root)
    except ValueError as exc:
        raise AgentPolicyError(
            _policy_denial(policy, "private_pack_path_required", f"automation {label} must remain inside the profile exports directory")
        ) from exc
    return path


def cmd_knowledge_query(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    saved = db.get_scope(args.scope)
    if saved is None:
        raise ValueError("saved scope was not found")
    chat_ids = tuple(saved["chat_ids"])
    if is_automation_shell():
        chat_ids = _require_exact_knowledge_scope(args._agent_policy, chat_ids)
    result = knowledge_catalog_lookup(
        db,
        profile_id=cfg.profile,
        scope_id=args.scope,
        chat_ids=chat_ids,
        query=args.query,
        limit=args._agent_policy.result_limit or args.limit if is_automation_shell() else args.limit,
        filters=_filters_from_args(args, args._agent_policy),
    )
    return emit(args, {"operation": "knowledge_query", **result})


def cmd_research_create(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    scope = _research_scope_from_saved(db, cfg.profile, args.scope)
    timestamp = args.at or _now_timestamp()
    checkpoint = _checkpoint_from_args(args, timestamp)
    budgets = tuple(sorted({
        "item_limit": args.item_limit,
        "context_radius": args.context_radius,
        "token_budget": args.token_budget,
        "stage_budget": args.stage_budget,
        "retry_budget": args.retry_budget,
        "tool_call_budget": args.tool_call_budget,
        "safety_margin": 0,
    }.items()))
    session = ResearchSession(args.session_id, scope, args.purpose, budgets, checkpoint, timestamp, timestamp)
    view = db.create_research_session(profile_id=cfg.profile, scope_id=args.scope, session=session)
    return emit(args, {"operation": "research_session_create", "session": view})


def cmd_research_checkpoint(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    view = db.research_session_view(profile_id=cfg.profile, session_id=args.session_id)
    checkpoint = _checkpoint_from_args(args, args.at or _next_session_timestamp(view["updated_at"]))
    updated = db.append_research_checkpoint(profile_id=cfg.profile, session_id=args.session_id, checkpoint=checkpoint)
    return emit(args, {"operation": "research_session_checkpoint", "session": updated})


def cmd_research_list(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    sessions = db.list_research_sessions(profile_id=cfg.profile, scope_id=args.scope, limit=args.limit)
    return emit(args, {"operation": "research_session_list", "sessions": sessions})


def cmd_research_inspect(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    view = db.research_session_view(profile_id=cfg.profile, session_id=args.session_id)
    if is_automation_shell():
        _require_exact_knowledge_scope(args._agent_policy, tuple(view["scope"]["chat_ids"]))
    return emit(args, {"operation": "research_session_inspect", "session": view})


def cmd_research_resume(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    view = db.research_session_view(profile_id=cfg.profile, session_id=args.session_id)
    if is_automation_shell():
        _require_exact_knowledge_scope(args._agent_policy, tuple(view["scope"]["chat_ids"]))
    plans = _session_refresh_plans(db, cfg.profile, view)
    return emit(args, {"operation": "research_session_resume", "session": view, "refresh": plans})


def cmd_research_selective_refresh(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    view = db.research_session_view(profile_id=cfg.profile, session_id=args.session_id)
    # Evidence sets are immutable. This command reports exactly which members
    # need a separately authorized bounded refresh; it never rewrites a set.
    return emit(args, {"operation": "research_session_selective_refresh", "session_id": args.session_id, "refresh": _session_refresh_plans(db, cfg.profile, view)})


def cmd_research_expand(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    view = db.research_session_view(profile_id=cfg.profile, session_id=args.session_id)
    session_chats = tuple(view["scope"]["chat_ids"])
    chat_ids = tuple(args._agent_policy.chat_ids) if is_automation_shell() else session_chats
    scope = KnowledgeScope(cfg.profile, chat_ids)
    result = expand_cited_sources(
        db,
        scope=scope,
        citations=tuple(args.citation),
        filters=_filters_from_args(args, args._agent_policy),
        item_limit=args._agent_policy.result_limit or args.limit if is_automation_shell() else args.limit,
        context_radius=args.context,
        token_budget=args.token_budget,
    )
    return emit(args, {"session_id": args.session_id, **result})


def _research_scope_from_saved(db: Database, profile_id: str, scope_id: str) -> KnowledgeScope:
    saved = db.get_scope(scope_id)
    if saved is None:
        raise ValueError("saved scope was not found")
    return KnowledgeScope(profile_id, tuple(saved["chat_ids"]))


def _checkpoint_from_args(args: argparse.Namespace, timestamp: str) -> ResearchCheckpoint:
    return ResearchCheckpoint(
        summary=args.summary,
        decisions=tuple(args.decision),
        unresolved_questions=tuple(args.unresolved),
        evidence_set_ids=tuple(sorted(args.evidence_set)),
        created_at=timestamp,
    )


def _session_refresh_plans(db: Database, profile_id: str, view: dict[str, Any]) -> list[dict[str, Any]]:
    scope = KnowledgeScope(profile_id, tuple(view["scope"]["chat_ids"]))
    plans: list[dict[str, Any]] = []
    for evidence_set_id in view["checkpoint"]["evidence_set_ids"]:
        evidence = db.evidence_set_view(profile_id=profile_id, evidence_set_id=evidence_set_id)
        if evidence["scope"] != view["scope"]:
            raise ValueError("research session evidence set does not belong to its exact saved scope")
        members = []
        raw_versions: list[tuple[str, str]] = []
        for member in evidence["members"]:
            citations = tuple(source["citation"] for source in member["sources"])
            members.append((member, citations))
            for citation in citations:
                current = _current_raw_version(db, citation)
                if current is not None:
                    raw_versions.append((citation, current))
        from .knowledge_catalog import EvidenceMemberKind, EvidenceSetMember, EvidenceSetReference

        reference = EvidenceSetReference(
            evidence["evidence_set_id"], scope, evidence["purpose"], evidence["query"],
            tuple(EvidenceSetMember(member["member_id"], EvidenceMemberKind(member["kind"]), member["logical_id"], citations, member["version"]) for member, citations in members),
            evidence["summary"], evidence["created_at"], evidence["revision"], tuple(evidence["topics"]),
        )
        plans.append(plan_evidence_reuse(reference, VersionMap(raw=tuple(sorted(raw_versions)), wiki=())).as_json())
    return plans


def _current_raw_version(db: Database, citation: str) -> str | None:
    chat_id, message_id = _parse_citation(citation)
    rows = db.message_context(chat_id, message_id, radius=0, filters=SearchFilters(chat_id=chat_id))
    item = next((row for row in rows if row.message_id == message_id), None)
    if item is None:
        return None
    from hashlib import sha256

    return sha256(f"{item.timestamp}\0{item.text}".encode("utf-8")).hexdigest()


def _require_exact_knowledge_scope(policy: Any, expected_chat_ids: tuple[int, ...]) -> tuple[int, ...]:
    effective = tuple(sorted(policy.chat_ids))
    expected = tuple(sorted(expected_chat_ids))
    if effective != expected:
        raise AgentPolicyError(
            _policy_denial(policy, "knowledge_scope_narrowed", "session and catalog metadata require the complete exact saved scope")
        )
    return effective


def _parse_citation(value: str) -> tuple[int, int]:
    parts = value.split("/")
    if len(parts) != 6 or parts[:3] != ["tg:", "", "chat"] or parts[4] != "message":
        raise ValueError("citation must be an exact tg://chat/.../message/... reference")
    try:
        return int(parts[3]), int(parts[5])
    except ValueError as exc:
        raise ValueError("citation must be an exact tg://chat/.../message/... reference") from exc


def _now_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _next_session_timestamp(previous: str) -> str:
    current = _now_timestamp()
    if current > previous:
        return current
    parsed = datetime.fromisoformat(previous.replace("Z", "+00:00"))
    return (parsed + timedelta(microseconds=1)).isoformat().replace("+00:00", "Z")


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


def cmd_embedding_build(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    provider = local_embedding_provider(cfg)
    return emit(
        args,
        db.build_embedding_index(
            provider,
            filters=_embedding_filters(args),
            batch_size=args.batch_size or cfg.semantic.batch_size,
            max_batches=args.max_batches,
        ),
    )


def cmd_embedding_status(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    filters = _embedding_filters(args)
    try:
        provider = local_embedding_provider(cfg)
    except SemanticUnavailableError as exc:
        return emit(
            args,
            {
                "provider_available": False,
                "reason": exc.reason,
                "index": db.embedding_index_status(filters=filters),
            },
        )
    return emit(args, {"provider_available": True, "index": db.embedding_index_status(provider.metadata, filters)})


def cmd_embedding_rebuild(args: argparse.Namespace) -> int:
    cfg, db = services(args)
    provider = local_embedding_provider(cfg)
    return emit(
        args,
        db.rebuild_embedding_index(
            provider,
            filters=_embedding_filters(args),
            batch_size=args.batch_size or cfg.semantic.batch_size,
            max_batches=args.max_batches,
        ),
    )


def cmd_embedding_remove(args: argparse.Namespace) -> int:
    _, db = services(args)
    return emit(args, db.remove_embedding_index(model_identity=args.model_identity, all_models=args.all_models))


def _embedding_filters(args: argparse.Namespace) -> SearchFilters:
    return SearchFilters(
        chat_id=args.chat_id,
        since=args.since,
        until=args.until,
        media_type=args.media_type,
    ).normalized()


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


def cmd_agent_guide(args: argparse.Namespace) -> int:
    cfg = load_config(args.config, home=args.home, profile=args.profile)
    db = Database(cfg.db_path)
    guide = build_agent_guide()
    text = guide.human_prompt()
    health = db.health() if Path(cfg.db_path).exists() else {"available": False}
    return emit(args, {"profile": cfg.profile, "health": health, "guide": text, "agent_guide": guide.as_json()}, plain=text)


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


def _effective_sync_scope(scope: dict[str, Any] | None, policy: Any) -> dict[str, Any] | None:
    if not is_automation_shell() or scope is None:
        return scope
    return {
        **scope,
        "chat_ids": list(policy.chat_ids),
        "since": policy.since,
        "until": policy.until,
        "media_policy": policy.media_policy or "none",
    }


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
