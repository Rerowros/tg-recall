## Why

The archive already persists retryable work and schema state, but operators cannot reliably repair failed queues or prove that upgrades and all retrieval modes are consistent. A small operational layer is needed before the schema grows for wiki and vector indexes.

## What Changes

- Add ordered, transactional SQLite migrations with explicit schema history and compatibility diagnostics.
- Extend `doctor` with migration, queue, index, and orphaned-object findings that do not expose private content.
- Add filtered job status, explicit retry, repair, and stale-job recovery commands with dry-run previews where state changes are broad.
- Define retry/backoff behavior for transient failures while preserving terminal failures for inspection.
- Apply chat, sender, date, media, and link filters consistently across every current retrieval mode.
- Preserve existing CLI and JSON success contracts; new maintenance operations are additive.

## Capabilities

### New Capabilities

- `archive-maintenance`: Covers safe schema upgrades, diagnostics, job repair/retry, and consistent retrieval filters.

### Modified Capabilities

None. The canonical specs have not yet been archived.

## Impact

The change affects SQLite migration/storage code, queue repositories, `doctor`, `jobs` and index commands, search implementations, audit events, and maintenance tests. It must remain profile-aware and operate only on the selected local archive.
