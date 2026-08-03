# Security Policy

## Sensitive Data

`tg-recall` handles Telegram user-session files, API credentials, local archive databases, downloaded media, and transcripts. Treat all local state as sensitive.

Do not include session files, archive data, media, transcripts, real chat identifiers, or personal reports in issues, pull requests, logs, screenshots, or release artifacts.

## Reporting A Vulnerability

For a vulnerability that could expose local archive data, session material, credentials, or permit unintended Telegram actions, use GitHub private vulnerability reporting for this repository. Do not open a public issue with reproduction data that includes secrets or personal content.

Include the affected version, a minimal sanitized reproduction, impact, and mitigation ideas when available.

## Operational Response

If a Telegram session may have leaked, revoke it in Telegram Settings -> Devices, delete the affected local session file, and reauthorize. Review public Git history and release assets before assuming the exposure is contained.
