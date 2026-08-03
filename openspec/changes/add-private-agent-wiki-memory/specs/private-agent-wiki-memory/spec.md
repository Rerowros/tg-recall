## ADDED Requirements

### Requirement: Private evidence-backed wiki storage
The system SHALL store raw snapshots and synthesized wiki pages below the configured local archive directory and SHALL exclude them from Git and package distributions.

#### Scenario: Create a person page

- **WHEN** a compiler creates or updates a page for a Telegram participant
- **THEN** the page includes a stable participant identifier, snapshot identifier, update timestamp, cited evidence, and distinct observed and hypothesis sections

### Requirement: Incremental wiki compilation
The system SHALL compile only messages added after the previous successful snapshot for the same source scope.

#### Scenario: Process a newly synced chat delta

- **WHEN** a chat has new messages after its last wiki snapshot
- **THEN** the compiler creates a new immutable snapshot and a new page revision without rewriting the previous revision

### Requirement: Evidence and freshness
The system SHALL retain Telegram citations and freshness metadata for every derived assertion.

#### Scenario: Identify stale knowledge

- **WHEN** an agent reads a page compiled before new source messages were synced
- **THEN** the page is marked stale and provides its last snapshot identifier
