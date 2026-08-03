## Context

The repository is currently an empty workspace initialized with OpenSpec. The target product is a local Telegram archive assistant that can read Telegram chats available to the user's account, extract text and media, transcribe audio/video where possible, index the data, and let an AI assistant answer user tasks with references to original messages.

Telegram Bot API is not sufficient for this product because it does not provide full access to the user's personal chat history. The project needs a Telegram MTProto user client. Telegram Premium can improve transcription and download behavior, but the design must not depend exclusively on Premium-only behavior.

## Goals / Non-Goals

**Goals:**

- Build a local-first archive engine for Telegram chats, messages, media, and transcripts.
- Provide a practical CLI for authorization, sync, search, transcription, maintenance, and debugging.
- Add MCP after the core engine is stable so AI agents can query the archive through explicit, permissioned tools.
- Keep extracted data, Telegram sessions, and provider usage visible and controllable by the user.
- Preserve citations back to Telegram chat/message identifiers for every AI answer.

**Non-Goals:**

- Do not build a public multi-user SaaS in the MVP.
- Do not automate sending Telegram messages or acting on behalf of the user in chats.
- Do not attempt to access Telegram secret chats or chats unavailable to the user's account.
- Do not rely on Telegram Premium transcription as the only transcription path.
- Do not expose raw unrestricted database access through MCP.

## Decisions

### CLI first, MCP second

The primary MVP interface will be a local CLI. The MCP server will be added as a secondary interface over the same core service layer.

Rationale:
- CLI is better for first-run setup, Telegram login, session checks, database migrations, sync jobs, backfills, media downloads, and operational debugging.
- CLI can run long jobs predictably and expose progress, retry, and failure state without an AI agent needing to hold a tool call open.
- MCP is best once there is a stable archive API, because it can expose narrow tools such as `search_messages`, `get_message_context`, `list_chats`, and `ask_archive`.
- A shared core engine prevents CLI and MCP from becoming two separate implementations.

Alternatives considered:
- MCP-only: rejected for MVP because Telegram authorization, long-running sync, rate-limit handling, and bulk media processing are operational workflows.
- CLI-only forever: rejected because the target use case is AI task execution over Telegram data, where MCP provides a clean tool boundary for agents.

### Python application with a layered core

Use Python for the MVP because Telegram MTProto, transcription orchestration, local data processing, and CLI tooling are well supported.

Proposed module layout:

```text
tg_recall/
  config/          # settings, profiles, provider policy
  telegram/        # MTProto session, chat discovery, history sync
  storage/         # SQLite schema, repositories, migrations
  media/           # media download, hashing, audio extraction
  transcription/   # Telegram transcription and fallback STT providers
  indexing/        # FTS, metadata filters, embeddings
  assistant/       # retrieval and answer context assembly
  cli/             # command line entrypoint
  mcp/             # MCP server entrypoint, added after core API stabilizes
```

Alternatives considered:
- Node.js: viable, but Python has a stronger fit for Telethon, local transcription tooling, and data processing.
- Direct scripts without a core package: rejected because CLI and MCP must share behavior.

### Telegram access through MTProto user client

Use an MTProto client library, preferably Telethon for the MVP, to authenticate as the user's account and read chats available to that account.

Rationale:
- The product needs historical message access and personal chat access, which Bot API does not provide.
- MTProto exposes chat history, message metadata, media references, and user-account-scoped features.
- Telethon has mature primitives for iterating history, downloading media, and handling Telegram errors such as flood waits.

### Local SQLite storage for MVP

Use SQLite as the first storage backend with explicit migrations. Store message metadata and normalized text in tables, use FTS for keyword search, and keep downloaded media in a content-addressed local file directory.

Rationale:
- SQLite is easy to run locally, works well for a personal archive, and keeps deployment simple.
- FTS is enough for a strong first search layer.
- The schema can later be moved to Postgres if multiple concurrent users, remote deployment, or heavier indexing becomes necessary.

### Incremental sync and queued processing

Separate sync into idempotent stages:

```text
chat discovery -> message sync -> media download -> transcription -> indexing
```

Each stage records status and retry state. History sync keeps a per-chat watermark and can backfill older ranges. Media downloads and transcription run through queues so Telegram rate limits and provider limits can be handled independently.

### Transcription provider strategy

Use a provider chain:

1. Telegram transcription for eligible voice/audio messages when available.
2. Local or cloud fallback speech-to-text for audio/video files.
3. Persist a failure state when transcription is not possible.

Transcripts must record provenance: provider, source media ID, language when known, timestamps if available, and error metadata for failures.

### Retrieval and AI task flow

The assistant flow will be retrieval-first:

```text
user task -> search plan -> retrieve messages/transcripts/files -> assemble cited context -> call LLM -> answer with citations
```

The system should never send the whole archive to an LLM. It retrieves bounded evidence and includes citations to chat/message IDs and local media references.

### Privacy and provider boundaries

Telegram session files, downloaded media, database files, and logs are treated as sensitive local data. External providers for LLM, embeddings, OCR, or STT must be explicitly configured, and the CLI must show what data classes can leave the machine.

MCP tools start read-only by default and only expose scoped operations. Destructive maintenance commands stay in CLI until there is a clear permission model.

## Risks / Trade-offs

- Telegram `FLOOD_WAIT` or account restrictions -> Use conservative defaults, adaptive backoff, one to three concurrent chats, and separate queues for media/transcription.
- Telegram Premium transcription is unavailable or inconsistent -> Keep fallback STT providers and cache all transcript results.
- Media archives can become large -> Use chat allowlists, date ranges, media type filters, content hashing, and storage usage commands.
- External STT/LLM providers can leak private data -> Require explicit provider configuration and document which content is sent externally.
- SQLite can become limiting for very large archives -> Keep repository boundaries and migrations clean so Postgres can be introduced later.
- MCP can expose too much data to agents -> Start with narrow read-only tools and enforce chat/date/type scopes in the core layer.

## Migration Plan

1. Initialize Python package, configuration, and SQLite migrations.
2. Implement CLI setup, Telegram auth, chat listing, and scoped sync.
3. Add media download queue, transcription queue, and indexing.
4. Add local search and cited answer context assembly.
5. Add MCP read/query tools over the same core service layer.
6. Add hardening: rate-limit tuning, provider policy, purge commands, and operational docs.

Rollback is simple during MVP: stop jobs, keep the Telegram session intact, and remove or recreate the local archive database/media directory if needed.

## Open Questions

- Which STT fallback should be the first supported provider: local Whisper, OpenAI transcription, or both behind one interface?
- Should semantic search use a local embedding model by default, or start with keyword/metadata search and make embeddings opt-in?
- Should the first UI be CLI-only, or should a small local web dashboard be added after the CLI stabilizes?
- Which chats should be included in the first real sync: explicit allowlist only, or interactive selection during setup?
