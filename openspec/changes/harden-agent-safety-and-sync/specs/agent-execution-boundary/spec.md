## ADDED Requirements

### Requirement: Explicit agent operation allowlist
The system SHALL allow automated agents to read scoped archive data, run scoped sync, download explicitly selected media, and transcribe selected media, and SHALL deny authorization, configuration or credential mutation, purge, restore, migration, and Telegram write operations.

#### Scenario: Agent requests a forbidden command
- **WHEN** automation invokes Telegram authorization, `config set`, purge, restore, migration, or a Telegram write operation
- **THEN** the system rejects the operation before opening a Telegram session or mutating local state and returns a stable permission error

#### Scenario: Agent performs an allowed materialization
- **WHEN** automation materializes media for an allowed `tg://` citation within the configured policy
- **THEN** the system may download only the media referenced by that citation and records an audit event

### Requirement: Uniform scoped archive reads
The system SHALL apply the intersection of configured AI access and requested chat, date, media, and result limits to every agent-visible CLI and MCP read, including metadata listings.

#### Scenario: Agent searches without explicit chat scope
- **WHEN** automation requests archive search without an explicit allowed chat
- **THEN** the system denies the request without searching the full archive

#### Scenario: MCP lists metadata while access is disabled
- **WHEN** MCP archive access is disabled and a client requests chats or scopes
- **THEN** the system returns a permission error and no archive metadata

#### Scenario: Requested scope exceeds policy
- **WHEN** a requested date range, media type, or result limit exceeds the configured boundary
- **THEN** the system narrows the request to the allowed intersection or denies it when the intersection is empty

### Requirement: Monotonic sync watermarks
The system SHALL preserve existing sync watermarks on empty runs, advance the newest watermark only forward, and advance the oldest watermark only backward.

#### Scenario: No new messages
- **WHEN** an incremental sync returns no messages
- **THEN** the stored newest and oldest message identifiers remain unchanged

#### Scenario: Historical backfill completes a page
- **WHEN** a backfill observes messages older than the stored oldest message
- **THEN** only the oldest watermark moves backward and the newest watermark is preserved

### Requirement: Auditable policy decisions
The system SHALL audit allowed and denied automated operations without storing secrets, raw message content, or unrestricted query text.

#### Scenario: Scoped read is denied
- **WHEN** an agent targets a chat outside the allowed policy
- **THEN** the audit records operation, profile, requested scope identifiers, decision, and reason without message content

### Requirement: Stable machine-readable failures
The system SHALL return deterministic JSON error envelopes for policy denial, invalid scope, and retryable Telegram failures.

#### Scenario: JSON policy denial
- **WHEN** a denied agent command is invoked with `--json`
- **THEN** the response preserves `ok=false` and contains a stable error code and safe message without a traceback or secret
