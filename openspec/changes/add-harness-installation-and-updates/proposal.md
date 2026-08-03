## Why

`tg-recall` has a read-only MCP server and a structured agent guide, but users must currently discover release updates and manually copy different MCP and prompt configuration into every AI harness. A privacy-preserving, versioned installer is needed so Codex, Claude Code, Cursor, and generic MCP clients receive the same safe workflow without configuration drift or silent archive access.

## What Changes

- Add cached, opt-in release checks against GitHub Releases with stable machine-readable results and no access to Telegram data.
- Add an explicit human-only update workflow that detects the installation source and either applies a supported update safely or prints an exact supported command without pretending success.
- Add idempotent `integrate` commands for install, status, refresh, preview, and uninstall across Codex, Claude Code, Cursor, and generic MCP clients.
- Generate every harness prompt/rule from the same versioned agent-guide model, including Spark/Luna routing, bounded retrieval, citations, and the existing safety matrix.
- Merge only owned MCP entries and marked instruction blocks, preserving unrelated user configuration and creating recoverable backups before mutation.
- Advertise concise workflow instructions from MCP initialization for clients that support server instructions, while retaining the strictly read-only MCP tool surface.
- Correct runtime agent-guide version reporting so generated integrations reflect the installed package rather than the legacy `0.2.0` label.
- Keep all mutating update and integration operations outside MCP and reject them in detected AI/automation shells.

## Capabilities

### New Capabilities

- `release-update-management`: Privacy-aware release discovery, installation-source detection, explicit safe updates, caching, and stable CLI/JSON contracts.
- `multi-harness-integration`: Idempotent installation and lifecycle management for MCP configuration and versioned instructions in Codex, Claude Code, Cursor, and generic harnesses.

### Modified Capabilities

None.

## Impact

The change affects CLI parsing and dispatch, package/version metadata, MCP initialization metadata, platform-specific configuration adapters, cached update state below `platformdirs`, documentation, and test fixtures. It may add a small HTTP client dependency only if the standard library cannot satisfy bounded GitHub checks. It does not read or copy archive, session, credential, profile, media, wiki, or export contents, does not auto-download the archive, and does not expose any new mutating MCP tool.
