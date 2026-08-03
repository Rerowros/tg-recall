## 1. Migration Foundation

- [x] 1.1 Add a migration history table and ordered in-code migration registry with checksums.
- [x] 1.2 Bootstrap existing v0.2, fresh, and supported legacy schemas without changing archive record identities.
- [x] 1.3 Execute each migration transactionally and add fixtures for success, rollback, repeat execution, and newer-than-supported versions.

## 2. Diagnostics

- [x] 2.1 Add storage checks for schema compatibility, integrity, queue health, index freshness, and orphaned media metadata.
- [x] 2.2 Extend `doctor` human and JSON output with sanitized stable findings and backup guidance.

## 3. Queue Operations

- [x] 3.1 Move job queries behind storage APIs and add stage/status/retryability/chat/age/limit filters.
- [x] 3.2 Define retry/backoff transitions and implement explicit bounded retry for eligible selected jobs.
- [x] 3.3 Implement dry-run repair detection for stale processing state, missing queue entries, and inconsistent retry metadata.
- [x] 3.4 Add audited apply mode for repair and tests that permanent failures and future `retry_after` jobs are not silently requeued.

## 4. Retrieval Consistency

- [x] 4.1 Introduce one normalized filter contract for chat, sender, date, media type, and link presence.
- [x] 4.2 Apply the shared filters to keyword, transcript, token-overlap semantic, export, and context retrieval paths.
- [x] 4.3 Add cross-mode contract tests proving identical filter boundaries and stable JSON fields.

## 5. Validation

- [x] 5.1 Document migration backup/rollback and queue repair workflows.
- [x] 5.2 Run migration/job/search tests, full pytest, backup/restore tests, distribution privacy checks, and strict OpenSpec validation.
