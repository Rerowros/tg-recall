## 1. Project Foundation

- [x] 1.1 Create Python package structure for config, Telegram, storage, media, transcription, indexing, assistant, CLI, and MCP modules
- [x] 1.2 Add project configuration for formatting and linting alongside dependency management and test execution
- [x] 1.3 Implement configuration loading with local profiles, secret redaction, and provider policy settings
- [x] 1.4 Add SQLite migration system and initial schema for chats, messages, media, transcripts, jobs, indexes, scopes, and audit events
- [x] 1.5 Add content-addressed local media storage layout with path helpers and disk usage reporting
- [x] 1.6 Add structured logging and local audit event writer that never logs raw secrets

## 2. Telegram Ingestion

- [x] 2.1 Implement MTProto session creation and reusable Telegram authorization flow
- [x] 2.2 Add CLI command to verify Telegram session health without printing session secrets
- [x] 2.3 Implement chat discovery with stable chat identifiers, display names, chat types, and eligibility flags
- [x] 2.4 Implement saved sync scopes for explicit chat and date range selection
- [x] 2.5 Implement complete incremental message sync with per-chat progress and historical backfill support
- [x] 2.6 Normalize messages with sender metadata, timestamps, edits, forwards, replies, links, and media references
- [x] 2.7 Implement Telegram flood wait handling with persisted retry time and resumable jobs
- [x] 2.8 Add tests for chat discovery, scoped sync refusal, message normalization, and flood wait handling using mocked Telegram responses

## 3. Media And Transcription

- [x] 3.1 Implement media download job creation based on chat scope, media type policy, and message references
- [x] 3.2 Implement media downloader with deduplication by Telegram metadata and content hash
- [x] 3.3 Add transcribable media detection for voice messages, audio files, and video files
- [x] 3.4 Implement Telegram transcription provider with success, unavailable, rejected, and retryable failure states
- [x] 3.5 Implement fallback speech-to-text provider interface and one initial provider adapter
- [x] 3.6 Add video audio extraction for fallback transcription when video contains an audio stream
- [x] 3.7 Persist transcripts with provider provenance, source media linkage, language when known, timestamps when available, and failure metadata
- [x] 3.8 Add tests for media dedupe, transcription fallback, transcript caching, and failure retry rules

## 4. Archive Indexing And Retrieval

- [x] 4.1 Implement keyword indexing for message text and transcript text
- [x] 4.2 Implement metadata filters for chat, sender, date range, media type, and link presence
- [x] 4.3 Add optional semantic indexing behind an explicit embedding provider configuration
- [x] 4.4 Implement cited retrieval results with chat ID, message ID, timestamp, transcript ID, and media ID where available
- [x] 4.5 Update indexes incrementally after message sync, media download, and transcription completion
- [x] 4.6 Implement assistant context assembly that retrieves bounded evidence and preserves citations
- [x] 4.7 Add tests for keyword search, metadata filters, incremental indexing, and cited context assembly

## 5. CLI Product Surface

- [x] 5.1 Add CLI setup command for configuration file creation and profile updates
- [x] 5.2 Add CLI commands for Telegram auth, session check, chat listing, and scope selection
- [x] 5.3 Add CLI commands for message sync, media download, transcription, and index rebuild
- [x] 5.4 Add CLI commands for job status, retry, and failed job inspection
- [x] 5.5 Add CLI search command that prints cited matching messages and transcripts
- [x] 5.6 Add CLI ask command that retrieves cited evidence and calls the configured LLM provider only within policy
- [x] 5.7 Add CLI purge command for selected chats, media, transcripts, indexes, and full archive reset
- [x] 5.8 Add local usage documentation for safe setup, Telegram limits, provider policy, and recommended sync settings

## 6. MCP Agent Interface

- [x] 6.1 Add MCP server entrypoint that loads the same configuration and core services as the CLI
- [x] 6.2 Implement read-only MCP tool for listing allowed chats and available scopes
- [x] 6.3 Implement read-only MCP tool for searching messages and transcripts with citations
- [x] 6.4 Implement read-only MCP tool for retrieving context around a cited message
- [x] 6.5 Implement read-only MCP tool for asking archive questions through bounded retrieval
- [x] 6.6 Enforce chat, date, and media-type scopes for every MCP tool call
- [x] 6.7 Audit MCP tool calls with tool name, scope, timestamp, and result count
- [x] 6.8 Add MCP integration tests that verify read-only behavior and scope denial

## 7. Validation And Hardening

- [x] 7.1 Add fixtures for Telegram-like messages, media records, transcripts, and archived jobs
- [x] 7.2 Add end-to-end local test that syncs fixture data, indexes it, searches it, and assembles cited answer context
- [x] 7.3 Add rate-limit simulation tests for sync, media download, and transcription queues
- [x] 7.4 Add privacy tests that confirm secrets are redacted from CLI output, logs, and audit events
- [x] 7.5 Add backup and restore notes for the local database, media directory, and Telegram session file
- [x] 7.6 Run OpenSpec validation and update artifacts until the change is apply-ready
