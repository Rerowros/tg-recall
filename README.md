# tg-recall

[English](README.md) | [Русский](README.ru.md)

Local-first Telegram archive for people and AI agents. `tg-recall` stores only explicitly selected chats in a local profile, indexes message text and transcripts, and returns source citations such as `tg://chat/.../message/...`.

> Early alpha (current release: v0.6.0). The archive includes private conversations and a Telegram user session. Keep the profile local, use full-disk encryption, and verify important findings against Telegram.

## Install

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```powershell
git clone https://github.com/Rerowros/tg-recall.git
cd tg-recall
uv sync --extra dev
uv run tg-recall setup
```

For normal use, install the published universal wheel from the matching GitHub
Release. GitHub displays the asset SHA-256 digest; compare it before installing
when your release process requires an independent integrity check. A GitHub
asset digest is integrity metadata, not a package signature.

```powershell
uv tool install https://github.com/Rerowros/tg-recall/releases/download/v0.6.0/tg_recall-0.6.0-py3-none-any.whl
tg-recall --json doctor
```

`uv tool install --editable .` is for development only. It deliberately keeps
the checkout as the source of the command, so `tg-recall update apply` will
report `manual_required` rather than overwrite that checkout. Update an
editable development install from its original checkout (`git pull`, `uv sync`
or the project's documented workflow), or reinstall a verified release wheel.

Self-update and harness installation are explicit human-only lifecycle
operations; they are never run automatically. Release checks are disabled by
default. Only after explicit periodic configuration can an eligible interactive
non-JSON CLI command make a best-effort check; help, MCP, automation, lifecycle
commands and JSON commands never do so. See
[harness integration](docs/harness-integration.md) for `update` / `integrate`
usage, scope support, backups, and manual fallbacks.

## Stable AI-harness bootstrap

To have Codex, Claude Code, Cursor, or another agent prepare a safe harness
setup plan, copy this exact instruction:

```text
Fetch https://raw.githubusercontent.com/Rerowros/tg-recall/main/docs/agent-setup/prompt.md and follow it.
```

The link is permanent and intentionally has no release version. The fetched
contract resolves the latest stable GitHub Release and an exact universal wheel
with its GitHub SHA-256 digest; it never installs from `main`. An agent may use
only `integrate list`, `integrate preview`, and `integrate status` as data-blind
diagnostics. Package installation and every `integrate install`, `refresh`, or
`uninstall` operation remain an explicit human command: the agent prints it
and stops. It requires an explicit `user` or `project` scope (and an explicit
project root for the latter), preserves manual/partial actions, and tells the
user to restart the affected harness before `status` verification.

The current published release can predate this contract. If its `integrate
list` command is unavailable, setup stops with a capability-gap message; it
must not use a checkout or source from `main` as a fallback.

`tg-ecosystem` and `tg-ecosystem-mcp` are deprecated compatibility aliases. Use `tg-recall` and `tg-recall-mcp` in new scripts; the aliases may be removed in a future breaking release.

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
tg-recall --json sync ensure work --chat -1001234567890 --since 2026-01-01 --media voice,photo --transcribe auto --limit 500
```

`transcribe auto` tries Telegram transcription first and falls back to local Whisper. `tg-recall --json doctor` checks the current archive, Telegram session, `ffmpeg` and the `whisper` executable.

## Local transcription

`transcribe run` defaults to `sidecar`; `telegram` uses Telegram, `local` uses
the selected local adapter, and `auto` remains Telegram-first. For typed,
human-only local backend settings, safe short-voice/long-audio VAD presets,
and one-citation verification, see [local transcription](docs/local-transcription.md).

## Agent Workflow

Ask an agent to start here:

```powershell
tg-recall agent guide
```

For bounded cited evidence:

```powershell
tg-recall --json retrieve --chat-id -1001234567890 --query "deadline" --context 8 --token-budget 12000
```

For a long-chat analysis, write a private JSONL export below the profile instead of printing the entire archive:

```powershell
tg-recall export --chat -1001234567890 --since 2026-01-01 --include transcripts,media-metadata --format jsonl
```

The CLI can sync, download and transcribe local archives. It does not expose agent commands for authentication, credential changes, purge or Telegram write operations. MCP remains read-only.

For a bounded, offline-verifiable handoff to a local AI workflow, create a separate pack; the existing `export` JSONL command is unchanged:

```powershell
tg-recall --json pack create project-a --chat -1001234567890 --since 2026-01-01 --max-records 200 --token-budget 12000
tg-recall --json pack inspect PATH\TO\project-a
tg-recall --json pack verify PATH\TO\project-a
```

`pack create` requires either a concrete chat plus a date boundary or a saved scope, and always requires positive record and token budgets. It writes under the selected profile's private `exports` directory by default. A human may explicitly pass `--output`; automation cannot. To include synthesized local knowledge, pass only explicit immutable `--wiki-revision` IDs (and `--wiki-scope` when it differs from the saved scope). Packs contain selected evidence and structured assertions, never sessions, credentials, media binaries, or absolute host paths.

Install managed Codex guidance instead of relying on Custom Instructions:

```powershell
tg-recall integrate install --target codex --scope user
```

Use this compact text only as a generic/manual fallback when managed harness
integration is unavailable:

```text
Если пользователь просит посмотреть Telegram-чат, используй локальный `tg-recall`: сначала выполни `tg-recall agent guide` и следуй его workflow только для запрошенных чатов. Разрешены sync, media download и transcription; запрещены auth, purge и изменение config. Выводы подтверждай ссылками `tg://`.
```

For cost-aware Codex model routing, progressive context budgets, and the full copy-ready prompt, see [docs/codex-agent-optimization.md](docs/codex-agent-optimization.md). The guide prefers available `gpt-5.3-codex-spark` for near-instant bounded search using its separate Codex limit, with `gpt-5.6-luna` as the stable low-cost fallback.

The repository also contains local-only [private wiki memory](docs/wiki-memory.md) and [private AI export packs](docs/ai-export-packs.md). Pack creation is a profile-local CLI integration; there is no MCP pack tool, automatic wiki compiler, or automatic whole-archive export.

## MCP

Start the local stdio server with:

```powershell
tg-recall-mcp
```

MCP is intentionally read-only and requires explicit `ai_access` configuration. It can list allowed cached chats and scopes, search local messages, return nearby context, and provide extractive cited retrieval.

`tg-recall-mcp` is a stdio process: it exits on stdin EOF, when the supervising parent process dies, after `TG_RECALL_MCP_UNUSED_TIMEOUT_SEC` seconds (default `600`) with no `tools/call`, or after `TG_RECALL_MCP_IDLE_TIMEOUT_SEC` seconds (default `1800`) without a request. Set a timeout to `0` to disable it, or set `TG_RECALL_MCP_PARENT_WATCHDOG=0` to disable parent reaping. Hosts may restart the server on the next call.

For optional genuine local-vector retrieval, first configure an already-downloaded model directory in the selected profile (`semantic.enabled=true`, `semantic.provider=sentence-transformers-local`, `semantic.model_path=PATH`) and install the optional runtime:

```powershell
uv sync --extra local-embeddings
tg-recall --json index embeddings build --chat-id -1001234567890 --max-batches 1
tg-recall --json index embeddings status --chat-id -1001234567890
tg-recall --json retrieve "deadline" --chat-id -1001234567890 --retrieval-mode auto --token-budget 8000
```

The model path must already exist locally; tg-recall never downloads a model. `auto` reports a keyword fallback when vectors are unavailable or stale. `semantic` is strict and returns `semantic_unavailable` rather than relabeling token overlap as vectors. `index embeddings rebuild` and `remove` are explicit human-only maintenance commands; MCP exposes only bounded `retrieve_evidence` and never builds, rebuilds, or removes an index.

## Current Limitations

- `ask` defaults to extractive cited retrieval. The optional OpenAI Responses provider requires its extra plus explicit provider-policy and scope approval; disabled or unavailable providers return a cited local fallback.
- Legacy `--semantic` uses local token overlap. Use `--retrieval-mode auto|hybrid|semantic` for the optional local embedding index.
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
