# tg-recall

Local-first Telegram archive for people and AI agents. `tg-recall` stores only explicitly selected chats in a local profile, indexes message text and transcripts, and returns source citations such as `tg://chat/.../message/...`.

> Early alpha. The archive includes private conversations and a Telegram user session. Keep the profile local, use full-disk encryption, and verify important findings against Telegram.

## Install

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```powershell
git clone https://github.com/Rerowros/tg-recall.git
cd tg-recall
uv sync --extra dev
uv run tg-recall setup
```

Install globally for Codex or another agent:

```powershell
uv tool install --editable .
tg-recall doctor --json
```

`tg-ecosystem` and `tg-ecosystem-mcp` remain deprecated aliases for one minor release.

## Local Storage

`tg-recall` never writes an archive into the repository by default.

| System | Config | Persistent data | State | Cache |
| --- | --- | --- | --- | --- |
| Windows | `%LOCALAPPDATA%\tg-recall\config` | `%LOCALAPPDATA%\tg-recall\data` | `%LOCALAPPDATA%\tg-recall\state` | `%LOCALAPPDATA%\tg-recall\cache` |
| Linux | `~/.config/tg-recall` | `~/.local/share/tg-recall` | `~/.local/state/tg-recall` | `~/.cache/tg-recall` |

Each Telegram account is a profile. Its SQLite archive, session, media objects, wiki and exports remain isolated below `data/profiles/<profile>/`. Media is content-addressed by SHA-256 and SQLite stores a relative key, so a profile can be restored on another OS.

Use a self-contained root for an encrypted external disk or portable setup:

```powershell
tg-recall --home D:\Private\tg-recall --profile work setup
```

Precedence is `--home`, `TG_RECALL_HOME`, then system defaults. Profile precedence is `--profile`, `TG_RECALL_PROFILE`, configured active profile, then `default`.

## Telegram Setup

Create API credentials at [my.telegram.org](https://my.telegram.org), then authorize from an interactive terminal:

```powershell
tg-recall config set telegram.api_id 123456
tg-recall config set telegram.api_hash "your_api_hash"
tg-recall config set telegram.phone "+10000000000"
tg-recall telegram auth
tg-recall telegram check
```

Credentials and the session receive best-effort private permissions. Use BitLocker on Windows or LUKS on Linux for encryption at rest.

## Archive Workflow

List chats, then create a policy-aware scope:

```powershell
tg-recall chats list
tg-recall scopes create work --chat -1001234567890 --since 2026-01-01 --media voice,photo --transcribe auto
tg-recall sync run work --limit 500
tg-recall sync run work --backfill --limit 500
```

`media` accepts `none`, `voice`, `audio`, `photo`, `video`, `document`, `all`, or a comma-separated combination. Text and media metadata are always indexed; selected source media is retained locally. Normal runs fetch only messages newer than the saved watermark; `--backfill` explicitly continues into older history.

For a single idempotent workflow:

```powershell
tg-recall sync ensure work --chat -1001234567890 --since 2026-01-01 --media voice,photo --transcribe auto --limit 500 --json
```

`transcribe auto` tries Telegram transcription first and falls back to local Whisper. `tg-recall doctor --json` checks the current archive, Telegram session, `ffmpeg` and the `whisper` executable.

## Agent Workflow

Ask an agent to start here:

```powershell
tg-recall agent guide
```

For bounded cited evidence:

```powershell
tg-recall retrieve --chat-id -1001234567890 --query "deadline" --context 8 --token-budget 12000 --json
```

For a long-chat analysis, write a private JSONL export below the profile instead of printing the entire archive:

```powershell
tg-recall export --chat -1001234567890 --since 2026-01-01 --include transcripts,media-metadata --format jsonl
```

The CLI can sync, download and transcribe local archives. It does not expose agent commands for authentication, credential changes, purge or Telegram write operations. MCP remains read-only.

Paste this compact instruction into Codex Custom Instructions:

```text
Если пользователь просит посмотреть Telegram-чат, используй локальный `tg-recall`: сначала выполни `tg-recall agent guide` и следуй его workflow только для запрошенных чатов. Разрешены sync, media download и transcription; запрещены auth, purge и изменение config. Выводы подтверждай ссылками `tg://`.
```

## MCP

Start the local stdio server with:

```powershell
tg-recall-mcp
```

MCP is intentionally read-only and requires explicit `ai_access` configuration. It can list allowed cached chats and scopes, search local messages, return nearby context, and provide extractive cited retrieval.

## Current Limitations

- `ask` is extractive cited retrieval; it does not call an LLM provider.
- `--semantic` uses local token overlap rather than embeddings.
- Local Whisper is invoked through an installed `whisper` executable; it is not bundled with the package.
- MCP cannot sync, download, transcribe, modify configuration or purge data.

## Migration And Backup

Copy a current `.tg-ecosystem` directory without changing its source:

```powershell
tg-recall migrate legacy --from C:\code\tg-ecosystem\.tg-ecosystem --dry-run
tg-recall migrate legacy --from C:\code\tg-ecosystem\.tg-ecosystem
```

The migration validates SQLite, copies media, converts absolute media paths to relative object keys, and never removes the legacy archive.

```powershell
tg-recall backup create --mode essential --output D:\Backups\tg-recall-essential.zip
tg-recall backup create --mode full --include-session --output D:\Backups\tg-recall-full.zip
tg-recall backup restore D:\Backups\tg-recall-essential.zip --profile restored
```

`essential` includes the database, profile configuration and wiki. `full` also contains media. Session and credentials are excluded unless `--include-session` is explicit.

## Development

```powershell
uv run pytest -q
uv build
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [ROADMAP.md](ROADMAP.md), and [docs/backup-restore.md](docs/backup-restore.md).
