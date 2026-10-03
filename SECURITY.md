# Security Policy

[English](SECURITY.md) | [Русский](SECURITY.ru.md)

## Sensitive Data

`tg-recall` handles Telegram user-session files, API credentials, local archive databases, downloaded media, and transcripts. Treat all local state as sensitive.

Do not include session files, archive data, media, transcripts, real chat identifiers, or personal reports in issues, pull requests, logs, screenshots, or release artifacts.

## Automated-Agent Boundary

`ai_access` controls what AI agents can reach: every MCP call, and CLI calls from an agent shell (`CLAUDECODE`, `AI_AGENT` or `CODEX_*` without a TTY, `TG_RECALL_AI_MODE=1`, `CI`). Agents see only allowed chats: `ai_access.allowed_chat_ids`, or every archived chat with `ai_access.allow_all_chats`. Requested chat, date, media and result bounds are intersected with `ai_access`; an empty intersection is denied before archive rows are read or a Telegram client opens. Agents can never change the configuration.

| Automated operation | Policy |
| --- | --- |
| `chats`, `search`, `read` (MCP and CLI), `export` | Allowed chats only, within `allowed_since`, `allowed_until`, `allowed_media_types` and result caps; agent exports stay below the profile `exports` directory. |
| `sync` (MCP and CLI), auto-refresh before `search` / `read` | Only with `ai_access.allow_sync=true` and only for allowed chats. Reads Telegram and writes only the local archive; agents cannot queue media. |
| `media materialize`, `transcribe run --citation` | CLI only, for one permitted `tg://` citation and an allowed media type. |
| `setup`, `telegram auth` / `check`, `chats --refresh`, `config`, `purge`, `backup`, `index`, `security`, `jobs`, `media usage` / `download`, `transcribe run` without `--citation` | Denied to agents. |
| Sending, editing or marking messages as read in Telegram | Not implemented for anyone. |

Policy allow/deny events record operation and safe scope identifiers only. They
never retain raw query text, message content, credentials, or session data.

## Reporting A Vulnerability

For a vulnerability that could expose local archive data, session material, credentials, or permit unintended Telegram actions, use GitHub private vulnerability reporting for this repository. Do not open a public issue with reproduction data that includes secrets or personal content.

Include the affected version, a minimal sanitized reproduction, impact, and mitigation ideas when available.

## Operational Response

If a Telegram session may have leaked, revoke it in Telegram Settings -> Devices, delete the affected local session file, and reauthorize. Review public Git history and release assets before assuming the exposure is contained.
