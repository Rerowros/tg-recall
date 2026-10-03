# Roadmap

[English](ROADMAP.md) | [Русский](ROADMAP.ru.md)

`v0.7.0` is the current release. It narrows tg-recall to its core: a local Telegram archive and a few cheap tools (`search`, `read`, `chats`, `sync`) for you and your AI agents over MCP or the CLI.

## Product principles

- Local-first: the archive, session, credentials, media and exports stay in the selected private profile.
- Read-only on Telegram: tg-recall never sends, edits or marks messages as read.
- Explicit agent access: agents see nothing until the owner enables `ai_access`; they cannot change configuration, log in or delete data.
- Cheap for agents: compact text sized to a token budget, with `tg://` citations back to the source messages.
- Small surface: reasoning, summaries and long-term memory belong to the calling agent, not to tg-recall.

## History

- `v0.1.0`: local SQLite archive, Telegram authorization, full-text search with citations, CLI and a read-only MCP server.
- `v0.2.0`: renamed to `tg-recall`; profiles, portable `--home` mode and content-addressed media.
- `v0.5.0`: hardened agent policy, archive maintenance (`doctor`, `jobs`) and schema migrations.
- `v0.6.0`: Russian documentation and configurable local transcription.
- `v0.7.0`: the core release. LLM answers, embeddings, research sessions, wiki memory, export packs, harness installers, self-update and sync scopes were removed; compact agent tools, forum topics, fast `sync` and agent auto-refresh were added. See the [changelog](CHANGELOG.md).

## Next

Future work follows real use; open an issue with your case. Current candidates:

- Publish every release to PyPI as well as GitHub Releases.
- Record a short demo for the README.
- Keep agent output small as MCP clients change, measured on real archives.

Privacy regressions, wider agent access than configured, lost citations and backup incompatibility block a release.
