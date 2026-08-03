## Context

The CLI and MCP already share the same configuration and database, but they apply different policy checks. Automation can reach several read commands without the `ai_access` boundary, while MCP metadata tools do not consistently require enabled access. Sync state also replaces both watermarks with values from the latest iterator, including `None` on an empty pass.

## Goals / Non-Goals

**Goals:**

- Define one policy decision for every agent-visible operation.
- Keep safe agent sync/media/transcription workflows usable within explicit user scope.
- Make forward and historical progress monotonic and resumable.
- Preserve existing command names and successful JSON payloads.

**Non-Goals:**

- Add Telegram write operations or autonomous scope discovery.
- Change human interactive authorization and maintenance workflows beyond explicit agent denial.
- Read, rewrite, or migrate existing archive content.

## Decisions

### Central operation policy

Introduce an operation enum/capability table consumed by CLI and MCP dispatch. Each operation declares whether it is read-only, agent-allowed, scope-required, state-changing, or human-only. The policy receives automation state, `ai_access`, requested chat/date/media scope, and result limit and returns a normalized decision.

This is preferred to scattered command checks because aliases and new subcommands otherwise drift. A separate agent executable was considered but rejected because it would duplicate parser and JSON contracts.

### Scope resolution before service execution

Agent reads MUST resolve to an explicit chat and the intersection of requested filters, configured `ai_access`, and any referenced saved scope. Metadata listings return only visible entries. Denial occurs before querying archive rows and is audited without raw query text.

### Monotonic sync progress

Forward and backfill watermarks are merged with stored state: a missing observation preserves the stored value, a forward run can only raise `newest_message_id`, and a backfill run can only lower `oldest_message_id`. Retry metadata is updated independently. This avoids schema expansion while fixing no-op behavior.

### Stable errors

JSON failures retain the existing envelope and add stable error codes/details additively. Human-readable errors remain concise. Successful payloads do not change in this milestone.

## Risks / Trade-offs

- Stricter enforcement can reject scripts that relied on archive-wide reads → document explicit `--chat-id`/scope migration and test interactive behavior separately.
- Saved sync scopes and AI allowlists can disagree → always take their intersection and explain the denied dimension without listing hidden scopes.
- Monotonic IDs assume Telegram message IDs increase within a chat → keep date bounds independent and test both forward and backfill paths.

## Migration Plan

No database migration is required. Land policy primitives and tests first, route MCP and CLI through them, then switch sync-state merging. Rollback restores the prior code without transforming user data.

## Open Questions

- Whether a future configuration should name allowed saved scopes directly in addition to chat IDs; this change supports intersection logic but does not add that field.
