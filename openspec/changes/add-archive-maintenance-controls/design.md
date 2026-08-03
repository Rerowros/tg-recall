## Context

`Database.migrate()` currently creates the latest schema and patches a small number of columns. Jobs persist status and retryability, but the CLI exposes only a recent unfiltered list. Future wiki and vector schemas need repeatable migrations, and current token-overlap search does not apply every advertised filter.

## Goals / Non-Goals

**Goals:**

- Make schema upgrades ordered, transactional, observable, and recoverable.
- Give operators precise queue diagnostics and explicit repair controls.
- Make filter semantics identical across retrieval implementations.
- Keep all maintenance profile-local and JSON-scriptable.

**Non-Goals:**

- Add a background daemon or silently retry every failed job.
- Move SQLite to a remote database.
- Download missing archive media during diagnostics.

## Decisions

### Ordered migration registry

Use an in-code ordered migration registry keyed by monotonically increasing version and record applied version/checksum/timestamp in SQLite. Each migration runs in one transaction after a consistent preflight. `doctor` reports newer-than-supported databases, incomplete migrations, integrity failures, and required backup guidance.

This is preferred to continuing `_ensure_column` patches because wiki/vector work will require tables and indexes with explicit upgrade order. A third-party migration framework is unnecessary for the current embedded schema.

### Repository-backed job operations

Move job listing/filter/retry/repair SQL behind storage methods. Retry operates only on explicitly selected retryable jobs; repair detects defined inconsistencies and supports preview before mutation. Permanent failures are never requeued implicitly. Every mutation writes an audit record and deterministic count summary.

### Shared filter compiler

One filter normalizer and SQL/in-memory predicate contract covers chat, sender, date, media type, and link presence. Keyword, transcript, token-overlap, export, and future vector candidates must pass the same normalized filter before ranking.

### Diagnostic data minimization

Maintenance reports use counts, identifiers, statuses, versions, hashes, and sanitized error classes. They do not emit message text, transcript text, credentials, session paths, or absolute media paths by default.

## Risks / Trade-offs

- A failed migration can leave SQLite locked → use one transaction, short lock time, and actionable rollback/backup guidance.
- Manual retry can cause provider or Telegram load → enforce retryability and `retry_after`, require explicit selection, and cap batches.
- Shared filters can change ordering/counts in current semantic mode → preserve ranking within the correctly filtered candidate set and add contract tests.

## Migration Plan

Bootstrap migration history by recognizing the existing `v0.2.0` schema without rewriting rows, then apply later migrations sequentially. Validate on fresh, current, and sanitized legacy fixtures. Rollback restores a pre-migration backup; destructive down-migrations are not provided.

## Open Questions

- Whether essential backup creation should become an automatic precondition for migrations once the archive exceeds a configurable size.
