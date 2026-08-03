## 1. Stable Agent Setup Contract

- [x] 1.1 Add the permanent versionless `docs/agent-setup/prompt.md` with a positive contract version, latest-release discovery, exact wheel/digest guidance, and no mutable-source installation.
- [x] 1.2 Add Codex, Claude Code, Cursor, and explicit generic setup branches that preserve the CLI capability matrix and manual actions.
- [x] 1.3 Require data-blind diagnostics, human-executed writes, restart/status verification, and explicit failure when the latest release lacks integration support.

## 2. Read-only Integration Discovery

- [x] 2.1 Refine the early lifecycle gate so AI/CI/automation may run only `integrate list`, `preview`, and `status`.
- [x] 2.2 Preserve early unconditional denial for integration writes and every update action without trusted-automation or flag bypass.
- [x] 2.3 Prove allowed discovery does not load Telegram configuration, credentials, session, SQLite, media, wiki, exports, network, subprocesses, or MCP.

## 3. Public Documentation

- [x] 3.1 Add the stable copy-paste `Fetch .../main/docs/agent-setup/prompt.md` instruction to README and harness documentation without a release number in the URL.
- [x] 3.2 Explain the human write boundary, latest-release requirement, current-release capability gap, supported harness differences, and restart workflow.

## 4. Tests And Privacy

- [x] 4.1 Add prompt contract tests for the stable URL, release-backed installation, supported harnesses, forbidden private/setup actions, and absence of automation-environment bypasses.
- [x] 4.2 Update lifecycle tests for allowed read-only AI discovery and still-denied writes/updates, including no-write and early-boundary assertions.
- [x] 4.3 Extend distribution/privacy coverage for the public prompt without allowing generated harness files, integration state/backups, package caches, or Telegram-private paths into artifacts.

## 5. Verification

- [x] 5.1 Run focused setup-prompt, lifecycle-boundary, MCP, and distribution tests.
- [x] 5.2 Run Ruff, full pytest, package build/inspection, CLI JSON smoke checks, `git diff --check`, and strict OpenSpec validation.
- [x] 5.3 Review the complete diff for stable-link behavior, prompt injection/supply-chain risk, privacy, JSON compatibility, and preservation of unrelated harness content.
