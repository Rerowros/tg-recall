# Compatibility

[English](compatibility.md) | [Русский](compatibility.ru.md)

From v1.0.0 tg-recall keeps the surface below compatible across every 1.x release. A 1.x release may add to it; it does not remove or narrow anything. Something that has to go is first marked deprecated in the changelog and keeps working at least until the next minor release; it is removed only in 2.0.

## What stays compatible

- **MCP tools**: the names `search`, `read`, `stats`, `export`, `chats`, `sync`, `transcribe`, their arguments and the values they accept. New optional arguments may appear; an argument never becomes required and never accepts less. `sync` and `transcribe` stay hidden until the owner enables them; `export` disappears when the owner sets its limit to 0.
- **CLI**: command names and options. With `--json`, an error is always `{"ok": false, "error": {"code": …, "message": …}}`; successful results may gain fields.
- **Citations**: `tg://chat/<chat_id>/message/<message_id>`. A citation printed by any 1.x release is accepted by every later one.
- **Configuration**: the keys under `telegram`, `ai_access` and `transcription`, for example `ai_access.allowed_chat_ids`. A new key never widens agent access by default. Keys of removed features in older profiles are ignored, not an error.
- **Archive and backups**: an archive or a `backup create` file from any release since v0.2.0 opens or restores in every 1.x release; the database migrates forward on the first start. The backup format is `tg-recall-backup` version 1.
- **MCP protocol**: the server answers in the protocol revision the client asks for among `2025-11-25`, `2025-06-18`, `2025-03-26` and `2024-11-05`, otherwise in the newest one; it answers `ping` and rejects malformed lines without exiting.

## What may change in any release

- The wording and layout of tool output and descriptions, and their token cost. Agents read the text; scripts should use `--json` or the citations.
- The ranking of search results.
- The database layout. Use the tools, `export` or backups rather than SQL.
- Python modules inside `tg_recall`: they are not a library API.

Downgrades are not supported: an older release refuses an archive whose schema is newer than it knows. Make a backup before upgrading.

## How it is checked

- `tests/test_public_contract.py` compares the current tools, commands, configuration keys, citation and backup format with `tests/fixtures/contract.json`. Removing or changing something fails as a breaking change; an addition fails until the snapshot is refreshed, so every change is deliberate.
- `tests/test_upgrade_compat.py` restores backups that v0.2.0, v0.6.0 and v0.8.1 wrote themselves (`scripts/make_upgrade_fixture.py`) and checks messages, transcripts, citations and agent settings after the migration.
- CI runs on Linux and Windows.
