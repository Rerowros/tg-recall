# Security Policy

[English](SECURITY.md) | [Русский](SECURITY.ru.md)

## Sensitive Data

`tg-recall` handles Telegram user-session files, API credentials, local archive databases, downloaded media, and transcripts. Treat all local state as sensitive.

Do not include session files, archive data, media, transcripts, real chat identifiers, or personal reports in issues, pull requests, logs, screenshots, or release artifacts.

## Automated-Agent Boundary

`ai_access` controls what AI agents can reach: every MCP call, and CLI calls from an agent shell (`CLAUDECODE`, `AI_AGENT` or `CODEX_*` without a TTY, `TG_RECALL_AI_MODE=1`, `CI`). Agents see only allowed chats: `ai_access.allowed_chat_ids`, or every archived chat with `ai_access.allow_all_chats`. Requested chat, date, media and result bounds are intersected with `ai_access`; an empty intersection is denied before archive rows are read or a Telegram client opens. Agents can never change the configuration.

| Automated operation | Policy |
| --- | --- |
| `chats`, `search`, `read`, `stats` (MCP and CLI) | Allowed chats only, within `allowed_since`, `allowed_until`, `allowed_media_types` and result caps. |
| `export` (MCP and CLI) | Allowed chats only, within the same date and media bounds and at most `ai_access.max_export_messages` messages (`0` removes the MCP tool). Agent exports are written only inside the profile `exports` directory; only the owner may choose another path. |
| `sync` (MCP and CLI), auto-refresh before `search` / `read` / `stats` / `export` | Only with `ai_access.allow_sync=true` and only for allowed chats. Reads Telegram and writes only the local archive; agents cannot queue media. An MCP download that outlasts `sync_max_seconds` continues in the background of the MCP server process, one at a time, up to `sync_background_minutes`. |
| `transcribe` (MCP) | Off unless the owner sets `ai_access.allow_transcribe=true`. At most 5 citations per call, each in an allowed chat, inside the date bounds and of an allowed media type; only voice, audio and video are transcribed. Downloads that media from Telegram and transcribes it with the owner-configured local provider, or asks Telegram to transcribe it. |
| `media materialize`, `transcribe run --citation` | CLI only, for one permitted `tg://` citation and an allowed media type. |
| `setup`, `telegram auth` / `check`, `chats --refresh`, `config`, `purge`, `backup`, `index`, `security`, `jobs`, `usage`, `media usage` / `download`, `transcribe run` without `--citation` | Denied to agents. |
| Sending, editing or marking messages as read in Telegram | Not implemented for anyone. |

Policy allow/deny events record operation and safe scope identifiers only. They
never retain raw query text, message content, credentials, or session data.

The usage log is local audit data: one `agent_call` row per MCP call and per CLI tool call from an agent shell, with the tool, client, arguments (query, chats, dates, limits), output token estimate, latency and error code. It keeps arguments, including search queries, but never message text, credentials or session data. Only the owner can read it (`tg-recall usage`).

## Reporting A Vulnerability

For a vulnerability that could expose local archive data, session material, credentials, or permit unintended Telegram actions, use GitHub private vulnerability reporting for this repository. Do not open a public issue with reproduction data that includes secrets or personal content.

Include the affected version, a minimal sanitized reproduction, impact, and mitigation ideas when available.

## Operational Response

If a Telegram session may have leaked, revoke it in Telegram Settings -> Devices, delete the affected local session file, and reauthorize. Review public Git history and release assets before assuming the exposure is contained.
