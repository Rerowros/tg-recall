# tg-ecosystem

Local-first Telegram archive with a CLI and a read-only MCP server for AI agents. It uses a Telegram MTProto user session to archive selected chats, index message text and transcripts, and return citations to the source message.

> Early alpha: use a dedicated local archive directory, keep backups encrypted, and verify results against Telegram before acting on them.

## What It Does

- Discovers chats available to the authorized Telegram account.
- Syncs explicitly named chat scopes into local SQLite storage.
- Queues media downloads and indexes message text and available transcripts.
- Searches messages and transcripts by keyword and metadata filters.
- Exposes cited read-only archive retrieval through MCP.

## Privacy Model

- This is a Telegram user-session tool, not a Bot API client.
- `.tg-ecosystem/` contains the session, archive database, downloaded media, transcripts, and derived local reports. Never commit or upload it to public storage.
- External LLM and transcription providers are disabled by default.
- Agent/automation access is read-only, opt-in, and constrained by the configured `ai_access` policy in this release.
- Run the following after setup to apply best-effort private file permissions:

```powershell
uv run tg-ecosystem security check --fix
```

## Install

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```powershell
git clone https://github.com/Rerowros/tg-ecosystem.git
cd tg-ecosystem
uv sync --extra dev
uv run tg-ecosystem setup
```

Set Telegram API credentials from [my.telegram.org](https://my.telegram.org):

```powershell
uv run tg-ecosystem config set telegram.api_id 123456
uv run tg-ecosystem config set telegram.api_hash "your_api_hash"
uv run tg-ecosystem config set telegram.phone "+10000000000"
uv run tg-ecosystem telegram auth
uv run tg-ecosystem telegram check
```

## Archive Workflow

List chats and copy the required `chat_id` from the first column:

```powershell
uv run tg-ecosystem chats list
```

Create an explicit scope, sync it, and process queued work:

```powershell
uv run tg-ecosystem scopes create work --chat -1001234567890 --since 2026-01-01
uv run tg-ecosystem sync run work --limit 500
uv run tg-ecosystem media download --limit 50
uv run tg-ecosystem transcribe run --telegram --limit 50
uv run tg-ecosystem search "deadline" --chat-id -1001234567890
```

Use `jobs` and `media usage` to inspect local processing state:

```powershell
uv run tg-ecosystem jobs
uv run tg-ecosystem media usage
```

## MCP

Start the local stdio server with:

```powershell
uv run tg-ecosystem-mcp
```

The MCP surface is intentionally read-only in v0.1.0. It provides allowed-chat listing, scope listing, cited message search, nearby message context, and extractive archive retrieval. Configure `ai_access.enabled`, `ai_access.allowed_chat_ids`, and `ai_access.max_results` before an automation client can read the archive.

## Current Limitations

- MCP cannot sync chats, download media, transcribe, modify configuration, or purge data.
- `ask` returns extractive cited evidence; it does not call an LLM provider yet.
- Local Whisper is not yet a built-in transcription provider. Without `--telegram`, the current fallback reads a sidecar transcript file next to downloaded media.
- `--semantic` uses local token overlap, not embedding-based semantic retrieval.
- Sync state is recorded, but the current sync command does not yet provide a complete incremental/backfill workflow.
- Repeated syncs can create duplicate media-download jobs; this is tracked as a public roadmap item.

## Backup And Restore

See [docs/backup-restore.md](docs/backup-restore.md). If a Telegram session file may have leaked, revoke it from Telegram Settings -> Devices and reauthorize locally.

## Development

```powershell
uv run pytest -q
uv build
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and [ROADMAP.md](ROADMAP.md).
