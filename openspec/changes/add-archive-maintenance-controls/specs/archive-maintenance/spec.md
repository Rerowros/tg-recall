## ADDED Requirements

### Requirement: Ordered transactional schema migrations
The system SHALL apply each local SQLite schema migration exactly once in version order and record migration version, checksum, and completion time.

#### Scenario: Upgrade a v0.2 archive
- **WHEN** the selected profile opens an older supported schema
- **THEN** pending migrations run transactionally in order without changing message, transcript, media, or citation identity

#### Scenario: Migration fails
- **WHEN** a migration step raises an error
- **THEN** that step is rolled back, later steps do not run, and the archive remains diagnosable

### Requirement: Compatibility and integrity diagnostics
The system SHALL report schema compatibility, SQLite integrity, queue health, index freshness, and orphaned storage metadata without exposing private content.

#### Scenario: Database is newer than the application
- **WHEN** `doctor` encounters an unsupported newer schema version
- **THEN** it reports an incompatible status and does not attempt a downgrade

#### Scenario: Healthy profile
- **WHEN** migrations, integrity, queues, indexes, and object references are consistent
- **THEN** `doctor --json` reports stable machine-readable checks with no message or transcript text

### Requirement: Filtered job inspection
The system SHALL list jobs by stage, status, retryability, chat, age, and bounded limit with stable JSON fields.

#### Scenario: Inspect retryable transcription failures
- **WHEN** the user filters jobs by transcription stage and retryable failure status
- **THEN** only matching jobs are returned with identifiers, sanitized error summary, retry time, and update time

### Requirement: Explicit retry and repair
The system SHALL retry or repair only explicitly selected eligible jobs and SHALL provide a dry-run preview for multi-job repair.

#### Scenario: Retry before backoff expires
- **WHEN** a retryable job has a future `retry_after`
- **THEN** the system refuses normal retry unless an explicit human-only override is supplied

#### Scenario: Repair stale queue state
- **WHEN** a dry-run detects a stale processing job or a missing derived queue entry
- **THEN** it reports the proposed state transitions without modifying the archive

#### Scenario: Permanent failure selected
- **WHEN** the user selects a non-retryable permanent failure
- **THEN** the system leaves it unchanged and reports why it was skipped

### Requirement: Consistent retrieval filters
The system SHALL apply chat, sender, date, media type, and link filters consistently before ranking keyword, transcript, token-overlap, export, and future semantic candidates.

#### Scenario: Semantic search with sender and date filters
- **WHEN** semantic retrieval receives sender and date constraints
- **THEN** every returned message satisfies both constraints regardless of ranking mode

#### Scenario: Transcript media filter
- **WHEN** retrieval is limited to voice media
- **THEN** transcript matches linked to other media types are excluded

### Requirement: Audited maintenance mutations
The system SHALL audit migration, retry, and repair mutations using counts and identifiers without recording raw archive content.

#### Scenario: Retry batch completes
- **WHEN** an operator requeues selected eligible jobs
- **THEN** the audit records actor mode, profile, selected job IDs, changed/skipped counts, and reason
