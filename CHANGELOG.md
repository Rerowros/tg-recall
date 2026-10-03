# Changelog

[English](CHANGELOG.md) | [Русский](CHANGELOG.ru.md)

## Unreleased

- Make `tg-recall-mcp` exit on stdin EOF, supervising parent death, unused timeout (`TG_RECALL_MCP_UNUSED_TIMEOUT_SEC`, default `600`), or idle timeout (`TG_RECALL_MCP_IDLE_TIMEOUT_SEC`, default `1800`). Set a timeout to `0` to disable it; set `TG_RECALL_MCP_PARENT_WATCHDOG=0` to disable parent reaping.
- Replace the MCP tools with `search`, `read` and `chats`: compact text, one line per message, sized to a token budget, with `tg://` citations. `read()` with no arguments returns what is new since this client's last read (first call: last 24h), a fair share per chat. The old `list_allowed_chats`, `list_scopes`, `search_messages`, `get_message_context`, `ask_archive` and `retrieve_evidence` tools are removed; research-session tools are exposed only with `ai_access.mcp_research_tools=true`.
- MCP rejects unknown arguments with a did-you-mean hint, accepts `chat_id` as an alias of `chats`, and picks up config edits without a restart.
- Select forum topics by t.me link (`t.me/<username>/<topic>`, `t.me/c/<id>/<topic>`) or `<chat>/<topic>`; `chats()` lists topics. Topics are stored per message with their titles.
- Add `ai_access` keys `max_read_messages` (`200`), `mcp_research_tools`, `instructions_list_chats`, `allow_sync` (all `false`) and `sync_max_seconds` (`50`); `max_results` now defaults to `20`.
- Add an optional MCP `sync` tool (only with `ai_access.allow_sync=true`): downloads up to 3 allowed chats or topics since a date (default `30d`) into the local archive, reads Telegram only, time-boxed and resumable.
- Add `tg-recall sync chat <target> [--since 30d] [--max-seconds 3600]`: fills one chat or forum topic from a date to now in one interruption-safe run without the 1000-message cap; a topic is fetched alone instead of the whole group. Writes are batched per page; a lock file makes a second process on the same Telegram session fail with `busy`.
- Store forwards as a short origin label; `telegram check` records the owner's id so agent output shows the owner as `я`.
- `--json` output is compact (no indentation) when run by an AI agent.

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
