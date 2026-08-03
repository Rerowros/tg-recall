## ADDED Requirements

### Requirement: Layered unified knowledge catalog
The system SHALL expose one profile-aware query interface over raw messages/transcripts, versioned wiki knowledge, and saved evidence sets while identifying each result's layer and authority.

#### Scenario: Query finds prior wiki and raw evidence
- **WHEN** the same subject exists in a wiki page and old Telegram messages
- **THEN** the result returns a compact wiki hit with freshness plus expandable citations to the authoritative raw messages

### Requirement: Raw archive remains authoritative
The system SHALL treat Telegram messages and transcripts as the source of truth and SHALL NOT overwrite them with wiki assertions, session summaries, Codex memories, or model output.

#### Scenario: Summary conflicts with a message
- **WHEN** a saved summary conflicts with current cited raw evidence
- **THEN** the system marks the derived record contradicted or stale and returns the raw evidence for verification

### Requirement: Resumable local research sessions
The system SHALL persist profile-local research sessions containing purpose, scope, budgets, evidence-set references, compact checkpoints, decisions, freshness, and timestamps without storing hidden model reasoning or full Codex transcripts.

#### Scenario: Resume an older task
- **WHEN** an agent resumes a named research session after a new Codex chat starts
- **THEN** it receives the compact checkpoint, prior evidence references, stale/current status, and next unresolved questions

### Requirement: Old source expansion
The system SHALL resolve every supported derived assertion or evidence reference to bounded original Telegram context using stable citations.

#### Scenario: Inspect an old decision
- **WHEN** an agent expands a cited decision from a previous session
- **THEN** the system returns the original old message window within current access policy and without reading unrelated chats

### Requirement: Freshness and selective invalidation
The system SHALL track source and wiki revision versions so new syncs invalidate only affected catalog entries, evidence sets, and research checkpoints.

#### Scenario: Unrelated chat is synced
- **WHEN** new messages arrive in a chat outside a saved session's scope
- **THEN** that session remains current and requires no recomputation

#### Scenario: Cited source is edited
- **WHEN** a cited Telegram message or transcript changes
- **THEN** dependent wiki assertions and evidence sets are marked stale until selectively refreshed

### Requirement: Private profile isolation
The system SHALL keep knowledge catalog metadata, research sessions, summaries, and evidence sets below the selected profile and exclude them from Git, wheel, sdist, logs, and release assets.

#### Scenario: Two profiles contain the same subject
- **WHEN** knowledge is queried in one profile
- **THEN** no result, session, citation, or freshness metadata from the other profile is visible

### Requirement: Codex memories are advisory only
The Codex prompt SHALL treat local Codex memories as optional navigation hints and SHALL verify Telegram-specific facts through the `tg-recall` knowledge catalog.

#### Scenario: Codex memory recalls an old claim
- **WHEN** a local Codex memory supplies a Telegram-related claim without current citations
- **THEN** the agent queries or expands `tg-recall` evidence before presenting the claim as confirmed
