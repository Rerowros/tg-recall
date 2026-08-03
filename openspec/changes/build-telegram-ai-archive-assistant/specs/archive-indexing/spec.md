## ADDED Requirements

### Requirement: Local archive schema
The system SHALL store chats, messages, media records, transcripts, jobs, and index metadata in a local database.

#### Scenario: Store synced message
- **WHEN** a message is synced from Telegram
- **THEN** the system stores the message and its relationships in the local database

### Requirement: Keyword and metadata search
The system SHALL provide search over message text, transcript text, chat, sender, date range, media type, and link presence.

#### Scenario: Search by phrase and date
- **WHEN** the user searches for a phrase within a date range
- **THEN** the system returns matching messages and transcripts from that date range

#### Scenario: Filter by media type
- **WHEN** the user searches for messages with video media
- **THEN** the system returns only results linked to video media records

### Requirement: Semantic retrieval
The system SHALL support semantic retrieval when an embedding provider and vector index are configured.

#### Scenario: Semantic search enabled
- **WHEN** embeddings are configured and indexed content exists
- **THEN** the system can return conceptually relevant messages even when exact keywords do not match

#### Scenario: Semantic search unavailable
- **WHEN** embeddings are not configured
- **THEN** the system continues to provide keyword and metadata search without failing

### Requirement: Cited retrieval results
The system SHALL return citations for every retrieved item, including chat identifier, message identifier, timestamp, and local media or transcript references when available.

#### Scenario: Retrieved transcript result
- **WHEN** search returns a transcript segment from a voice message
- **THEN** the result includes the source chat, source message, timestamp, and linked media identifier

### Requirement: Incremental index updates
The system SHALL update indexes incrementally when new messages, media, or transcripts are added.

#### Scenario: New transcript indexed
- **WHEN** a transcription job completes successfully
- **THEN** the transcript becomes searchable without requiring a full archive rebuild

### Requirement: Cited answer context
The system SHALL assemble bounded context for AI tasks from retrieved messages and transcripts, preserving citations in the prompt context.

#### Scenario: Ask archive question
- **WHEN** the user asks a question over the archive
- **THEN** the system retrieves relevant evidence and prepares context that includes citations for each evidence item
