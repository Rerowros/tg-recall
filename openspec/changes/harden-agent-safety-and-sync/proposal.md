## Why

`v0.2.0` has the intended read-only MCP surface and a safe agent workflow, but enforcement is not yet uniform across CLI reads, metadata tools, and sync state updates. These gaps can widen an automated agent's access or lose incremental progress, so they must be closed before adding synthesized memory.

## What Changes

- Apply one agent-execution policy to CLI and MCP reads, including chat allowlists, saved-scope date/media boundaries, result limits, and metadata listing.
- Explicitly deny agent execution of Telegram authorization, credential/config mutation, purge, backup restore, migration, and Telegram write operations.
- Preserve existing forward and backfill watermarks on empty runs and make progress updates monotonic.
- Standardize structured permission, scope, and retry errors without breaking successful JSON payloads.
- Add regression tests for scoped reads, forbidden operations, no-op sync, backfill, and flood-wait recovery.

## Capabilities

### New Capabilities

- `agent-execution-boundary`: Defines safe automated-agent operations, scoped archive reads, and monotonic sync progress.

### Modified Capabilities

None. The canonical specs have not yet been archived; this change narrows and completes the existing safety contract without a breaking CLI change.

## Impact

The change affects `security.py`, CLI command dispatch, MCP authorization, sync-state persistence, audit events, and focused security/sync/MCP tests. It does not read or migrate user archives and adds no external dependency.
