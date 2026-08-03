## ADDED Requirements

### Requirement: CLI setup commands
The system SHALL provide CLI commands for configuration, Telegram authorization, chat discovery, and sync scope selection.

#### Scenario: Configure project
- **WHEN** the user runs the setup command
- **THEN** the CLI creates or updates a local configuration file without requiring manual file editing

#### Scenario: Select chat scope
- **WHEN** the user selects chats through the CLI
- **THEN** the selected scope is saved for later sync commands

### Requirement: CLI operational commands
The system SHALL provide CLI commands for syncing messages, downloading media, running transcription, rebuilding indexes, searching, and asking archive questions.

#### Scenario: Run scoped sync
- **WHEN** the user runs the sync command for a saved scope
- **THEN** the CLI runs message sync and reports progress, counts, and failures

#### Scenario: Search archive
- **WHEN** the user runs a search command
- **THEN** the CLI prints matching results with citations to original Telegram messages

### Requirement: Job visibility
The system SHALL expose job status, retry state, and recent failures through the CLI.

#### Scenario: Inspect failed jobs
- **WHEN** the user requests failed job status
- **THEN** the CLI lists failed jobs with stage, affected chat or media, error summary, and retryability

### Requirement: Shared core API
The system SHALL implement CLI and MCP behavior through a shared core service layer rather than duplicating Telegram, storage, transcription, or search logic.

#### Scenario: CLI and MCP search consistency
- **WHEN** the CLI and MCP server run the same archive search with the same scope
- **THEN** both interfaces return equivalent results from the same core retrieval service

### Requirement: MCP read-only tools
The system SHALL expose MCP tools for read/query operations only in the first MCP release.

#### Scenario: MCP search messages
- **WHEN** an AI agent calls the MCP search tool with an allowed scope
- **THEN** the tool returns cited archive results without modifying Telegram or local archive state

#### Scenario: MCP disallowed operation
- **WHEN** an AI agent attempts to trigger a destructive or write operation through MCP
- **THEN** the system rejects the operation because the first MCP release is read-only

### Requirement: MCP scoped access
The system SHALL enforce configured chat, date, and media-type scopes for MCP tools.

#### Scenario: Agent queries outside scope
- **WHEN** an MCP request targets a chat outside the configured scope
- **THEN** the system denies the request and returns a scope error
