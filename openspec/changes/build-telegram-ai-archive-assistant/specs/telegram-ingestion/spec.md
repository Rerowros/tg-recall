## ADDED Requirements

### Requirement: MTProto account authorization
The system SHALL authenticate to Telegram as the user's account through MTProto and store the session locally.

#### Scenario: First authorization
- **WHEN** the user runs the authorization command with valid Telegram credentials
- **THEN** the system creates a reusable local Telegram session without requiring reauthorization on the next run

#### Scenario: Invalid authorization
- **WHEN** Telegram rejects the authorization attempt
- **THEN** the system reports the failure without creating or overwriting a valid existing session

### Requirement: Chat discovery
The system SHALL list Telegram dialogs available to the authorized account with stable identifiers and basic metadata.

#### Scenario: List available chats
- **WHEN** the user runs chat discovery
- **THEN** the system returns chat identifiers, display names, chat types, and sync eligibility flags

### Requirement: Scoped chat selection
The system SHALL sync only chats selected by explicit allowlist, command argument, or saved profile.

#### Scenario: Sync selected chat
- **WHEN** the user requests sync for one selected chat
- **THEN** the system syncs that chat and does not read unrelated chats

#### Scenario: Empty scope
- **WHEN** the user runs a sync command without a selected chat or saved sync profile
- **THEN** the system refuses to sync and explains how to select a scope

### Requirement: Incremental history sync
The system SHALL read message history incrementally and persist per-chat progress.

#### Scenario: Continue from prior progress
- **WHEN** a chat has already been partially synced
- **THEN** the next sync resumes from the stored progress instead of duplicating completed work

#### Scenario: Backfill older history
- **WHEN** the user requests a historical date range older than the current watermark
- **THEN** the system fetches the requested range and records progress separately from forward sync state

### Requirement: Message normalization
The system SHALL normalize Telegram messages into a local model that includes text, timestamps, sender metadata, reply relationships, forward metadata, edit state, links, and media references when available.

#### Scenario: Message with media and reply
- **WHEN** a Telegram message contains text, media, and a reply target
- **THEN** the stored message preserves the text, media reference, and reply relationship

### Requirement: Media download queue
The system SHALL enqueue media downloads separately from message sync and deduplicate downloaded files by Telegram metadata and content hash when available.

#### Scenario: Deferred media download
- **WHEN** message sync finds media that matches the configured media policy
- **THEN** the system creates a pending media job without blocking message ingestion

#### Scenario: Duplicate media
- **WHEN** the same media file is encountered more than once
- **THEN** the system stores one local file and links each message to the shared media record

### Requirement: Telegram rate-limit handling
The system SHALL detect Telegram flood wait responses and delay further requests for the required interval.

#### Scenario: Flood wait during sync
- **WHEN** Telegram returns a flood wait response
- **THEN** the system pauses the affected job, records the retry time, and resumes only after the wait period
