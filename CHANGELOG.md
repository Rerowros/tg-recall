# Changelog

[English](CHANGELOG.md) | [Русский](CHANGELOG.ru.md)

## v0.8.1 - 2026-10-03

### Fixed

- `stats(query=…)` also counts voice messages whose transcript matches, each message once, like `search`.
- `transcribe` uses a local speech-to-text executable configured by path (`transcription.executable`), not only one found on `PATH`. Checked live with Faster-Whisper-XXL on a GPU: two voice messages downloaded and transcribed in about 20 seconds.
- `sync` from an AI agent's shell (CLI, not MCP) runs for 90 seconds by default instead of an hour, so the agent's command is not cut off by its harness; it resumes on the next call. The owner's terminal keeps 3600 seconds.
- `tg-recall stats` no longer offers an unused `--budget` option.

### Added

- `scripts/agent_bench.py`: runs your own questions through a real agent (headless Claude Code with only the tg-recall tools) and records turns, tool calls, tool-result tokens, total tokens, cost, time and keyword checks; `--compare` shows the difference between two runs. Questions and results stay in your profile because they name your chats; `scripts/bench_questions.example.json` shows the format.

## v0.8.0 - 2026-10-03

Agents can now work with big periods cheaply: `stats` counts instead of reading, `export` writes a whole period to a file, and `sync` no longer blocks the agent. The owner can see how agents use the archive with `tg-recall usage`.

### Upgrade notes

- Upgrade: `uv tool install --force https://github.com/Rerowros/tg-recall/releases/download/v0.8.0/tg_recall-0.8.0-py3-none-any.whl`.
- The default of `ai_access.sync_max_seconds` changed from `50` to `20`: it is now how long one MCP `sync` call waits before the download continues in the background. A config saved by an earlier version keeps its value; to use the new default, run `tg-recall config set ai_access.sync_max_seconds 20`.
- `transcribe` is off until the owner sets `ai_access.allow_transcribe` to `true`. Agent `export` is on by default (up to `50000` messages per file); `tg-recall config set ai_access.max_export_messages 0` removes it.

### Added

- MCP `stats(chats, since, until, from, media, query, by)` and `tg-recall stats [--query] [--by day|week|month]` with the usual scope flags: messages per day, week or month (with `query`, hits per period), top senders, forum topics and chats. A few hundred tokens instead of reading thousands of messages.
- MCP `export(chats, since, until, from, media)`: writes a whole period or topic, full text, in `read`'s one-line format with a date on every line, to a file in the profile's `exports` directory. Returns the path, line count, token estimate and a suggested chunk size; agents read the file with their own file tools. Limited by `ai_access.max_export_messages` (default `50000`; `0` removes the tool). CLI: `tg-recall export --chat ID[/TOPIC] --format text` (the owner may pass `--output`).
- MCP `transcribe(refs)` for up to 5 `tg://` citations, only with `ai_access.allow_transcribe=true` (default `false`): downloads the cited voice, audio or video from Telegram and transcribes it with the configured local `transcription.*` provider when available, otherwise with Telegram's own transcription (needs Telegram Premium). Returns the text; transcripts become searchable.
- Usage log: every MCP call and every CLI tool call from an agent shell is recorded as an `agent_call` audit row with the tool, client, arguments (query, chats, dates, limits; never message text), output tokens, latency and error code. `tg-recall usage [--since 7d] [--client NAME]` (owner only) shows per tool calls, errors, empty results, average and max tokens and average latency, plus error codes, empty searches, identical calls within 120 s, searches retried after an empty result and `read` pages of 100+ messages.
- `ai_access.sync_background_minutes` (default `30`), `ai_access.max_export_messages` (default `50000`) and `ai_access.allow_transcribe` (default `false`).

### Changed

- MCP `sync` no longer blocks: a call waits `ai_access.sync_max_seconds` (default now `20`, was `50`), then the download continues in the background in the MCP server process (limit `ai_access.sync_background_minutes`) with progress, Telegram's message count for the chat or topic, and an ETA. While it runs, `search` and `read` add a note and skip auto-refresh. A bare `sync()` reports the current or last download (the last one for 10 minutes), otherwise it updates all allowed chats. One download runs at a time. The CLI `sync` still blocks and prints progress to stderr.
- `search` query syntax: words (all must match, then any), `a | b` alternatives (synonyms, other languages), `"exact phrase"` and `-word` to exclude. `stats` with `query` counts the messages that match it (all words, without the any-word fallback).
- The MCP initialize instructions point agents to `stats` and `export` for big periods and explain the background `sync`.

### Fixed

- A chat and topic reference like `Acme/157` no longer also selects a same-named chat that lacks that topic (for example a project's channel next to its forum).
- Forum messages archived before 0.7 get their topic on the next start: a reply to a topic root becomes membership, a message without a reply goes to General, replies inherit their parent's topic. On a real archive this placed 43.8k of 46k such rows; rows whose parent is not archived stay without a topic.
- `export` takes a forum topic (`--chat <chat>/<topic>`, `t.me/c/<id>/<topic>` or `--topic`), adds `topic_id` and `topic_title` to each row and no longer stops silently at 100,000 messages; with `--limit` it reports `truncated`.
- A long `sync` prints progress to stderr every 10 seconds, so it is visibly alive.

## v0.7.0 - 2026-10-03

tg-recall is now a small core: a local Telegram archive plus `search`, `read`, `chats` and `sync` for you and your AI agents, over MCP or the CLI. Features that the calling agent does better itself were removed. This is a breaking release.

### Upgrade notes

- Back up first: `tg-recall backup create --mode essential --output D:\Backups\tg-recall-before-0.7.zip`.
- On first run the archive is migrated: `semantic_index`, the embedding tables and sync scopes are dropped; wiki and research tables are dropped only when empty; then the file is compacted (`VACUUM`) when that frees a lot of space. Chats, messages, media and transcripts are kept.
- MCP clients now get `search`, `read` and `chats` (plus `sync` with `ai_access.allow_sync=true`) instead of `list_allowed_chats`, `list_scopes`, `search_messages`, `get_message_context`, `ask_archive` and `retrieve_evidence`. The server command is still `tg-recall-mcp`.
- Scopes are gone: replace `scopes create` with `sync run` / `sync ensure` by `tg-recall sync <chat> --since <date>`, and list the chats agents may use in `ai_access.allowed_chat_ids` (or set `ai_access.allow_all_chats`).
- Old config files still load; keys of removed features (`llm`, `semantic`, `provider_policy`, `ai_access.mcp_research_tools`) are ignored.
- Upgrade: `uv tool install --force https://github.com/Rerowros/tg-recall/releases/download/v0.7.0/tg_recall-0.7.0-py3-none-any.whl` (the self-updating `tg-recall update` is gone).

### Removed

- `ask` and `retrieve`, in-tool LLM answers and the OpenAI Responses provider.
- Semantic and hybrid search and embeddings (local sentence-transformers, OpenRouter): `config embeddings`, `index embeddings` and the per-message `semantic_index`. Search is local full-text search (SQLite FTS5).
- Research sessions, evidence sets and the knowledge catalog, including their MCP tools.
- Wiki memory; backups no longer include the wiki folder.
- Export packs (`pack`).
- `integrate` harness installers, `agent guide`, Codex model routing and the versionless agent-setup prompt.
- `update` self-update.
- `migrate legacy`, the `tg-ecosystem` / `tg-ecosystem-mcp` aliases and `TG_ECOSYSTEM_HOME`.
- Sync scopes (`scopes`, `sync run`, `sync ensure`) and the 1000-message cap per run.
- The `local-embeddings` and `openai-responses` extras, the `llm`, `semantic` and `provider_policy` config sections and `ai_access.mcp_research_tools`.

### Added

- Compact `search`, `read` and `chats` for MCP and the CLI: one line per message, sized to a token budget; `>` marks a hit, `↩N` a reply, and a `cite:` line lists `tg://` citations. `read()` without arguments returns what is new since this client's last read (first call: last 24h), a fair share per chat. With `--json` the CLI returns `{"text", "count", "chat_ids"}`. The owner at a terminal sees every archived chat; inside an AI agent shell only `ai_access` chats apply.
- Forum topics: select them by t.me link (`t.me/<username>/<topic>`, `t.me/c/<id>/<topic>`) or `<chat>/<topic>`; `chats` lists topics, and each message stores its topic and title.
- `tg-recall sync [TARGET...] [--since] [--max-seconds] [--media]`: brings chats or forum topics up to date over one Telegram connection. A chat never synced starts 30 days back, `--since` also fetches older history, and a topic is fetched alone instead of the whole group. Runs are interruption-safe and write in batches; FloodWait is saved and respected; `--media` queues media for download. Without targets it updates the allowlist, or every chat with messages.
- A lock file per Telegram session: a second process fails with `busy` instead of waiting.
- MCP `sync` tool (only with `ai_access.allow_sync=true`): reads Telegram only, stays within the allowlist and policy dates, is time-boxed by `sync_max_seconds` (`50`) and resumes on the next call. With `allow_sync`, agent `search` and `read` first pull new messages for chats synced more than `auto_refresh_minutes` (`10`) ago; failures (busy session, rate limit) only add a note.
- `ai_access` keys `allow_all_chats` (every archived chat is allowed), `max_read_messages` (`200`), `instructions_list_chats` (`false`), `allow_sync` (`false`), `sync_max_seconds` and `auto_refresh_minutes`; `max_results` now defaults to `20`. `config set` accepts list values as `1,2`, `[1, 2]` or `1 2`.
- MCP rejects unknown arguments with a did-you-mean hint, accepts `chat_id` as an alias of `chats`, and picks up config edits without a restart.
- `tg-recall-mcp` exits on stdin EOF, supervising parent death, unused timeout (`TG_RECALL_MCP_UNUSED_TIMEOUT_SEC`, default `600`) or idle timeout (`TG_RECALL_MCP_IDLE_TIMEOUT_SEC`, default `1800`). Set a timeout to `0` to disable it; set `TG_RECALL_MCP_PARENT_WATCHDOG=0` to disable parent reaping.
- Forwards are stored with a short origin label; `telegram check` records the owner's id so agent output shows the owner as `я`.
- `--json` output is compact (no indentation) when run by an AI agent.

### Fixed

- Never prompt for a Telegram login outside `telegram auth`: `doctor`, `sync` and MCP fail fast with a clear error when the session is missing.
- Write CLI and MCP stdio as UTF-8 regardless of the Windows console code page; MCP no longer answers JSON-RPC notifications.
- Treat Claude Code and Codex shells (`CLAUDECODE`, `AI_AGENT`, `CODEX_*` without a TTY) as automation, so agent policy applies to their CLI calls.
- Estimate tokens by character class (Cyrillic, Latin, digits, emoji) instead of one token per UTF-8 byte, so budgets fit about twice as much Russian text.

## v0.6.0 - 2026-08-04

- Add a permanent, versionless agent-setup prompt for Codex, Claude Code, Cursor, and generic harnesses, resolving only verified stable GitHub Release wheels.
- Allow automation to inspect `integrate list`, `preview`, and `status` without profile, archive, session, network, or harness writes; installation and update actions remain human-only.
- Add profile-local typed transcription settings and a shell-free Faster-Whisper-XXL adapter with explicit local executable/model checks, GPU/compute/VAD controls, bounded timeouts, and sanitized diagnostics.
- Keep `sidecar` as the transcription default and `auto` Telegram-first; agents still require one permitted explicit `tg://` citation and cannot change configuration.
- Add complete Russian versions of the main public documentation and synchronized EN/RU local-transcription guidance.
- Correct documented placement of the global `--json` flag and extend distribution tests so public docs ship while archives, credentials, sessions, media, wiki, and exports remain excluded.

## v0.5.0 - 2026-08-03

- Harden agent and MCP scope enforcement, preserve monotonic sync/backfill watermarks, and keep `FLOOD_WAIT` recovery resumable.
- Add transactional archive maintenance through schema v6, `doctor` diagnostics, filtered job inspection, explicit retry, and queue repair previews.
- Add budgeted context routing with Codex Spark/Luna guidance, immutable cited evidence sets, research sessions, and profile-local wiki memory.
- Add optional fully local hybrid retrieval with explicit embedding configuration, vector freshness, deterministic cited evidence, and keyword fallback.
- Add bounded private AI export packs with verifiable manifests; archives, sessions, media, wiki, exports, and packs remain private by default.
- Add opt-in OpenAI Responses synthesis with policy checks and cited local fallback when a provider is disabled or unavailable.
- Extend the compatible CLI and read-only MCP surface while preserving local-first privacy boundaries and JSON contracts.

## v0.2.0 - 2026-08-03

- Rename the public CLI and package to `tg-recall`; retain deprecated legacy executable aliases.
- Add platform-aware roots, portable `--home` mode and isolated Telegram profiles.
- Store new media by relative content-addressed keys instead of absolute filesystem paths.
- Add scope media/transcription policies, `sync ensure`, local Whisper detection, agent guide/retrieve/export/materialize commands, safe legacy migration and consistent backups.

## v0.1.0 - 2026-08-03

Initial public early-alpha release.

- Local SQLite archive, Telegram MTProto authorization, scoped sync, media queue, transcript storage, FTS search, and cited retrieval.
- CLI operational surface and a read-only local MCP server.
- Package-level privacy hardening: strict source-distribution allowlist and tests that reject non-public release contents.

Known limitations are documented in [README.md](README.md).
