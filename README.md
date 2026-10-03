# tg-recall

[English](README.md) | [Русский](README.ru.md)

[![CI](https://github.com/Rerowros/tg-recall/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Rerowros/tg-recall/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue.svg)](pyproject.toml)
[![PyPI](https://img.shields.io/badge/PyPI-coming%20soon-lightgrey.svg)](https://pypi.org/project/tg-recall/)
<!-- After the first PyPI release, replace the PyPI badge image with https://img.shields.io/pypi/v/tg-recall.svg -->

A local Telegram archive that your AI agents can query cheaply. `tg-recall` downloads the chats you choose into a local SQLite database and gives Claude Code, Codex, Cursor and other agents a few small tools over MCP or the CLI: `search`, `read`, `chats` and, if you allow it, `sync`. Answers are compact text, one line per message, sized to a token budget, with `tg://chat/<id>/message/<id>` citations back to the original messages.

It only reads Telegram: it never sends, edits or marks messages as read.

> Alpha (current release: v0.7.0). The archive holds private conversations and a Telegram user session: keep the profile local, use full-disk encryption and verify important findings in Telegram. Upgrading from v0.6 or older? v0.7.0 removed many features; read the [changelog](CHANGELOG.md) and make a backup first.

## Install

Requires Python 3.13+.

```powershell
uv tool install https://github.com/Rerowros/tg-recall/releases/download/v0.7.0/tg_recall-0.7.0-py3-none-any.whl
```

Install the universal wheel from the [GitHub Release](https://github.com/Rerowros/tg-recall/releases) (compare the SHA-256 digest GitHub shows). To upgrade, run the same command with the newer release URL plus `--force`. `uv tool install tg-recall` / `pip install tg-recall` will work once the package is on PyPI.

## Quick start

Create API credentials at [my.telegram.org](https://my.telegram.org), then in an interactive terminal:

```powershell
tg-recall setup
tg-recall config set telegram.api_id 123456
tg-recall config set telegram.api_hash "your_api_hash"
tg-recall config set telegram.phone "+10000000000"
tg-recall telegram auth
tg-recall chats --refresh
tg-recall sync -1001234567890 --since 2026-01-01
tg-recall search "deadline"
```

`chats --refresh` fetches your chat list from Telegram; plain `chats` lists archived chats with their forum topics (`--all` also shows chats without messages).

`sync` takes one or more targets: a chat id, a title fragment, a t.me link (`t.me/<username>/<topic>`, `t.me/c/<id>/<topic>`) or `<chat>/<topic>`. All targets go over one Telegram connection:

- A chat that was never synced starts 30 days back. `--since` (an ISO date or `30d`) also fetches older history; later runs fetch only new messages.
- A forum topic is fetched on its own, not the whole group.
- `--max-seconds` (default `3600`) limits one run; an interrupted run is safe, just run it again. A Telegram rate limit (FloodWait) is saved and respected on the next run.
- A lock file keeps a second process off the same Telegram session; it fails with `busy`.
- Without targets, `sync` updates the chats in `ai_access.allowed_chat_ids`, or every chat that already has messages.
- `--media voice,audio` (or `all`) also queues media; fetch it with `media download` and transcribe it with `transcribe run`.

`search`, `read`, `chats` and `sync` print the same compact text that agents get over MCP:

```text
2 hits · 2 chats · tz +04 · archive synced 5m ago · ~120 tok
## Work (-1001234567890)
-- 09-30 --
>1 05:19 Mark: deadline is friday
 2 05:24 я: ok, noted
## Partners (-1009876543210)
-- 10-03 --
>7 04:19 Ann: deadline moved to Monday
cite: tg://chat/-1001234567890/message/1 tg://chat/-1009876543210/message/7
```

`>` marks a hit, `↩N` a reply to message N, `…[+N]` a shortened message (`read REF --full` shows all of it), and the `cite:` line lists citations. `read` without arguments shows what is new since your last `read` (the first call covers the last 24 hours); `read --chat T --since 7d` reads a period; `read REF` reads around a citation. With `--json` these commands return `{"text", "count", "chat_ids"}`.

## Connect to Claude Code / Codex / Cursor

`tg-recall-mcp` is a stdio MCP server over the local archive. It returns nothing until you enable [AI access](#ai-access).

Claude Code:

```powershell
claude mcp add --scope user tg-recall -- tg-recall-mcp
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.tg-recall]
command = "tg-recall-mcp"
args = []
```

Cursor (`~/.cursor/mcp.json` or project `.cursor/mcp.json`) and other `mcpServers` JSON clients:

```json
{
  "mcpServers": {
    "tg-recall": { "command": "tg-recall-mcp", "args": [] }
  }
}
```

Set `TG_RECALL_PROFILE` in the server environment to use a non-default profile. Restart the client after changing its MCP config; `ai_access` edits are picked up by a running server without a restart.

`tg-recall-mcp` exits on stdin EOF, when the parent process dies, after `TG_RECALL_MCP_UNUSED_TIMEOUT_SEC` seconds (default `600`) without a `tools/call`, or after `TG_RECALL_MCP_IDLE_TIMEOUT_SEC` seconds (default `1800`) without a request. Set a timeout to `0` to disable it, or `TG_RECALL_MCP_PARENT_WATCHDOG=0` to disable parent reaping. Hosts may restart the server on the next call.

## AI access

Agents see nothing until you, the owner, allow specific chats:

```powershell
tg-recall config set ai_access.enabled true
tg-recall config set ai_access.allowed_chat_ids "-1001234567890,-1009876543210"
```

To allow every archived chat instead, set `ai_access.allow_all_chats` to `true`. To let agents fetch missing chats, topics or periods from Telegram themselves:

```powershell
tg-recall config set ai_access.allow_sync true
```

MCP tools:

- `search(query)`: hits with sender, time, nearby context and citations. Optional: `chats`, `since`, `until`, `from`, `media`, `context`, `limit`, `budget`.
- `read()`: new messages since this client's last read (first call: last 24 hours), a fair share per chat. `read(chats)` gives the latest messages, `read(chats, since, until)` a period, `read(refs)` windows around citations (`full=true` for uncut text).
- `chats()`: allowed chats with message counts, last activity, sync age and forum topics.
- `sync(chats, since)`: only with `allow_sync`. Brings chats or topics up to date (no `chats` means all allowed ones), reads Telegram only, limited to `sync_max_seconds` per call; a partial result continues on the next call.

`chats` accepts ids, title fragments, t.me links and `<chat>/<topic>`; `chat_id` works as an alias. Unknown arguments get a did-you-mean error. After `tg-recall telegram check` the owner's own messages are shown as `я`. With `allow_sync`, `search` and `read` first pull new messages for chats synced more than `auto_refresh_minutes` ago; if that fails (session busy, rate limit), the answer comes from the archive with a note.

The CLI follows the same rules. You at a terminal see every archived chat. Inside an AI agent shell (`CLAUDECODE`, `AI_AGENT` or `CODEX_*` without a TTY, `TG_RECALL_AI_MODE=1`), `search`, `read`, `chats` and `sync` see only `ai_access` chats.

What agents can do: search, read and list allowed chats; sync them when `allow_sync` is on; from the CLI, also `export` one allowed chat into the profile's `exports` directory and `media materialize` or `transcribe run --citation` for one allowed `tg://` citation.

What agents cannot do: change configuration or credentials, log in, refresh the chat list from Telegram, purge data, back up or restore, run queue or index maintenance, or send anything to Telegram. Message text reaches them as untrusted data, not instructions.

| `ai_access` key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Master switch |
| `allowed_chat_ids` | empty | Chats agents may use |
| `allow_all_chats` | `false` | Every archived chat is allowed (the list is ignored) |
| `max_results` | `20` | Max `search` hits |
| `max_read_messages` | `200` | Max messages per `read` |
| `allowed_since`, `allowed_until` | none | Date bounds for agents |
| `allowed_media_types` | `all` | Media types agents may see |
| `instructions_list_chats` | `false` | Put allowed chat titles into the MCP instructions (costs tokens in every session) |
| `allow_sync` | `false` | Enable `sync` and auto-refresh |
| `sync_max_seconds` | `50` | Time limit of one MCP `sync` call |
| `auto_refresh_minutes` | `10` | Refresh chats older than this before `search` and `read` (`0` turns it off) |

`config set` accepts list values as `1,2`, `[1, 2]` or `1 2`.

## Commands

| Command | What it does |
| --- | --- |
| `setup` | Create the profile config and database |
| `doctor` | Check the archive, schema, Telegram session and transcription tools |
| `config show`, `config set KEY VALUE` | Show the redacted config or change a value |
| `telegram auth`, `telegram check` | Log in (interactive) or check the saved session |
| `chats [QUERY] [--all] [--refresh]` | List chats and forum topics |
| `sync [TARGET...] [--since] [--max-seconds] [--media]` | Download new messages, and older history with `--since` |
| `search QUERY [--chat T]... [--since] [--until] [--from] [--media] [--context] [--limit] [--budget]` | Find messages with context and citations |
| `read [REF...] [--chat T]... [--since] [--until] [--before] [--after] [--full] [--limit]` | New messages, a period, or windows around citations |
| `export --chat ID` | Write one chat to JSONL in the profile's `exports` directory |
| `media usage`, `media download`, `media materialize --citation REF` | Media disk usage, download queued media, fetch the media of one message |
| `transcribe run` | Transcribe voice, audio and video (see [local transcription](docs/local-transcription.md)) |
| `jobs` | Inspect, retry or repair the media and transcription queue |
| `index rebuild` | Rebuild the full-text index |
| `security check [--fix]` | Check private file permissions |
| `backup create`, `backup restore` | Make a consistent ZIP backup or restore it into a profile |
| `purge --chat-id ID`, `purge --all` | Delete local archive data |

Global options go before the command: `--json`, `--profile NAME`, `--home PATH`, `--config PATH`. For example, `tg-recall --json doctor`.

## Local storage

`tg-recall` never writes an archive into the repository or the current directory by default.

| System | Config | Persistent data | State | Cache |
| --- | --- | --- | --- | --- |
| Windows | `%LOCALAPPDATA%\tg-recall\config` | `%LOCALAPPDATA%\tg-recall\data` | `%LOCALAPPDATA%\tg-recall\state` | `%LOCALAPPDATA%\tg-recall\cache` |
| Linux | `~/.config/tg-recall` | `~/.local/share/tg-recall` | `~/.local/state/tg-recall` | `~/.cache/tg-recall` |

Each Telegram account is a profile. Its SQLite archive, session, media and exports stay below `data/profiles/<profile>/`. Media is stored by SHA-256 with relative keys, so a profile can be restored on another OS.

Use a self-contained root for an encrypted external disk or a portable setup:

```powershell
tg-recall --home D:\Private\tg-recall --profile work setup
```

Precedence is `--home`, `TG_RECALL_HOME`, then system defaults. Profile precedence is `--profile`, `TG_RECALL_PROFILE`, the configured active profile, then `default`.

## Privacy and security

- Only the chats you sync are stored, and only on your machine. Search is local full-text search (SQLite FTS5); no text is sent to any AI provider by `tg-recall` itself.
- Credentials and the session get best-effort private file permissions (`security check --fix`). Use BitLocker on Windows or LUKS on Linux for encryption at rest.
- Agent policy decisions are audited with the operation and scope identifiers only, never the query text, message content, credentials or session.
- Local Whisper is not bundled; the `telegram` transcription provider asks Telegram to transcribe. See [local transcription](docs/local-transcription.md).
- If a session may have leaked, revoke it in Telegram Settings -> Devices and run `telegram auth` again. Report vulnerabilities as described in [SECURITY.md](SECURITY.md).

Back up with the built-in commands rather than copying a live database:

```powershell
tg-recall backup create --mode essential --output D:\Backups\tg-recall-essential.zip
tg-recall backup create --mode full --include-session --output D:\Backups\tg-recall-full.zip
tg-recall backup restore D:\Backups\tg-recall-essential.zip --profile restored
```

`essential` contains the database and profile configuration, `full` also the media. The session and credentials are included only with `--include-session`.

## Documentation

- [Changelog](CHANGELOG.md)
- [Local transcription](docs/local-transcription.md)
- [Backup and restore](docs/backup-restore.md)
- [Archive maintenance](docs/archive-maintenance.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [Roadmap](ROADMAP.md)

## Development

```powershell
git clone https://github.com/Rerowros/tg-recall.git
cd tg-recall
uv sync --extra dev
uv run pytest -q
uv build
```
