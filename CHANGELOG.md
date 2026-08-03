# Changelog

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
