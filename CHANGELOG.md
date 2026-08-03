# Changelog

[English](CHANGELOG.md) | [Русский](CHANGELOG.ru.md)

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
