## ADDED Requirements

### Requirement: Local secret storage
The system SHALL store Telegram sessions, API credentials, and provider keys locally and exclude them from logs and normal command output.

#### Scenario: Print configuration
- **WHEN** the user asks the CLI to show configuration
- **THEN** the CLI redacts secrets and does not print Telegram session data

### Requirement: Explicit data scope
The system SHALL require explicit chat and date scope before bulk sync, media download, transcription, or AI query operations.

#### Scenario: Bulk sync without scope
- **WHEN** the user attempts a bulk sync without an explicit scope
- **THEN** the system refuses and asks the user to select chats or date limits first

### Requirement: Provider data policy
The system SHALL show and enforce which data classes may be sent to external LLM, embedding, OCR, or speech-to-text providers.

#### Scenario: External transcription disabled
- **WHEN** external transcription is disabled by policy
- **THEN** the system does not send audio or video content to a cloud transcription provider

#### Scenario: External LLM enabled for selected scope
- **WHEN** external LLM usage is enabled for a selected scope
- **THEN** the system sends only retrieved bounded evidence for that scope, not the full archive

### Requirement: Secret chat exclusion
The system SHALL not ingest Telegram secret chats.

#### Scenario: Secret chat encountered
- **WHEN** chat discovery encounters a secret chat or unsupported encrypted chat type
- **THEN** the system marks it unsupported and excludes it from sync

### Requirement: Audit trail
The system SHALL keep a local audit log of sync, media download, transcription, provider calls, MCP queries, and destructive maintenance commands.

#### Scenario: MCP archive query
- **WHEN** an AI agent queries the archive through MCP
- **THEN** the system records the tool name, scope, timestamp, and result count without logging raw secrets

### Requirement: Local purge
The system SHALL provide a CLI maintenance command to purge selected chats, media files, transcripts, indexes, or the full archive.

#### Scenario: Purge one chat
- **WHEN** the user purges a selected chat
- **THEN** the system deletes that chat's local messages, transcripts, indexes, and linked media that is not referenced by other chats
