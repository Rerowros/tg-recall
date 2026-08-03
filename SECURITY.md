# Security Policy

## Sensitive Data

`tg-recall` handles Telegram user-session files, API credentials, local archive databases, downloaded media, and transcripts. Treat all local state as sensitive.

Do not include session files, archive data, media, transcripts, real chat identifiers, or personal reports in issues, pull requests, logs, screenshots, or release artifacts.

## Automated-Agent Boundary

`ai_access` is an explicit allowlist for automated CLI and MCP archive access.
An agent must name a permitted chat; requested date, media and result boundaries
are intersected with `ai_access` and with a saved sync scope. A missing or empty
intersection is denied before archive rows are read or a Telegram client opens.

| Automated operation | Policy |
| --- | --- |
| Cached allowed-chat/scope metadata; `search`, `ask`, `retrieve`, `export` | Allowed only within `ai_access` and an explicit chat scope; automation exports stay below the profile export directory. |
| `sync run` / `sync ensure` | Allowed only for the effective configured scope; no archive-wide sync. |
| `media materialize`, `transcribe run --citation` | Allowed only for one permitted `tg://` citation and allowed media type. |
| MCP | Read-only; it never authorizes, syncs, downloads, transcribes, or writes Telegram data. |
| `setup`, auth/check/discovery, config/credentials, purge, backup/restore, migration, index rebuild, unscoped jobs/media, Telegram writes | Denied to automation, including legacy command aliases. |

Policy allow/deny events record operation and safe scope identifiers only. They
never retain raw query text, message content, credentials, or session data.

## Reporting A Vulnerability

For a vulnerability that could expose local archive data, session material, credentials, or permit unintended Telegram actions, use GitHub private vulnerability reporting for this repository. Do not open a public issue with reproduction data that includes secrets or personal content.

Include the affected version, a minimal sanitized reproduction, impact, and mitigation ideas when available.

## Operational Response

If a Telegram session may have leaked, revoke it in Telegram Settings -> Devices, delete the affected local session file, and reauthorize. Review public Git history and release assets before assuming the exposure is contained.
