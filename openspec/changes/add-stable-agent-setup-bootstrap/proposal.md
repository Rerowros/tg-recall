## Why

`tg-recall` can configure several AI harnesses, but users must already know the
correct package, lifecycle command, harness name, and scope. A stable,
agent-readable setup URL can provide the same low-friction bootstrap pattern as
modern agent-friendly documentation without allowing an AI process to access
Telegram data or silently rewrite global configuration.

## What Changes

- Add one permanent, versionless raw GitHub URL whose Markdown prompt remains
  current on `main` and detects Codex, Claude Code, Cursor, or an explicit
  generic destination.
- Make the prompt resolve the latest published release and its integrity
  metadata at runtime instead of embedding a release number in the URL.
- Allow AI/automation shells to run only non-mutating `integrate list`,
  `integrate preview`, and `integrate status` diagnostics.
- Keep `integrate install`, `refresh`, and `uninstall`, every `update` command,
  package installation, and all Telegram/profile lifecycle operations
  human-executed. The prompt must stop with copy-ready commands before writes.
- Document a stable copy prompt, supported harness matrix, verification flow,
  restart requirements, and the difference between current `main` and the
  latest release.

## Capabilities

### New Capabilities

- `stable-agent-setup-bootstrap`: Covers the permanent fetch URL, agent-readable
  setup contract, release discovery, harness-specific instructions, and
  human-confirmed write boundary.
- `read-only-integration-discovery`: Covers safe AI access to integration
  inventory, preview, and status without Telegram/profile/config access or file
  mutation.

### Modified Capabilities

<!-- No archived base capability exists yet; this change supersedes the earlier
     active-change rule that denied read-only integration discovery. -->

## Impact

The change affects CLI lifecycle gating, integration tests, public setup
documentation, README onboarding, OpenSpec contracts, and distribution/privacy
checks. It adds no dependency, server, telemetry, remote MCP endpoint, package
installer, Telegram access, credential handling, or automatic configuration
write. The stable URL is public repository content and never contains private
paths or state.
