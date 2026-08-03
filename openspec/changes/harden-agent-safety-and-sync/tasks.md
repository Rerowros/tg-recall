## 1. Policy Foundation

- [x] 1.1 Define the centralized operation capability table and normalized policy decision/error model.
- [x] 1.2 Add scope intersection for AI allowlists, explicit chat/date/media filters, saved scopes, and result limits.
- [x] 1.3 Add sanitized allow/deny audit events and unit tests for policy decisions.

## 2. CLI And MCP Enforcement

- [x] 2.1 Route CLI search, ask, retrieve, export, metadata, sync, media, and transcription entrypoints through the shared policy.
- [x] 2.2 Deny auth, config/credential mutation, purge, restore, migration, and any Telegram write operation in agent mode, including legacy aliases.
- [x] 2.3 Route all MCP tools, including chat/scope listings, through the same enabled/scope checks.
- [x] 2.4 Add CLI and MCP tests for allowed workflows, metadata denial, scope narrowing, result caps, and forbidden operations.

## 3. Sync Correctness

- [x] 3.1 Merge forward and backfill watermarks monotonically while preserving stored values on empty runs.
- [x] 3.2 Keep retry metadata independent from message progress and test no-op, bounded-date, interrupted, and `FLOOD_WAIT` runs.

## 4. Contracts And Validation

- [x] 4.1 Add stable JSON error codes/details without changing successful v0.2 payload fields.
- [x] 4.2 Update agent guide and security documentation with the exact allowed/forbidden operation matrix.
- [x] 4.3 Run focused security/sync/MCP tests, full pytest, distribution privacy checks, and strict OpenSpec validation.
