## Why

Personal and work Telegram history contains decisions, files, links, voice messages, photos, and videos that are hard to search or summarize manually. The project will create a local AI-ready archive so the user can give an assistant tasks over Telegram chats while keeping account access and extracted data under local control.

## What Changes

- Add a Telegram MTProto-based ingestion system that authenticates as the user's Telegram account and reads chats available to that account.
- Normalize chat text, message metadata, replies, links, media references, and downloaded files into a local storage model.
- Add a media processing pipeline for photos, videos, voice messages, audio, and documents.
- Add transcription support for voice/audio/video using Telegram transcription when available and a fallback speech-to-text provider when Telegram transcription is unavailable or unsuitable.
- Add an indexed retrieval layer so AI tasks can search messages, transcripts, files, and chat metadata with citations back to Telegram message IDs.
- Add a local CLI as the primary MVP control surface for sync, indexing, querying, and maintenance.
- Design an MCP server as a secondary integration layer so AI agents can safely query the local archive through explicit tools after the core engine is stable.
- Add privacy, rate limiting, and session safety controls around Telegram session files, downloaded media, and LLM/STT provider usage.

## Capabilities

### New Capabilities

- `telegram-ingestion`: Connect to Telegram through MTProto, discover chats, sync history incrementally, and download selected media.
- `media-transcription`: Extract or generate searchable transcripts from Telegram voice messages, audio files, and video files.
- `archive-indexing`: Store normalized Telegram data locally and provide keyword, metadata, and semantic retrieval over messages and media-derived text.
- `assistant-interfaces`: Expose project operations through a local CLI first and an MCP server for AI-agent access after the core archive API is stable.
- `privacy-safety`: Protect Telegram sessions and local archive data with explicit scope controls, redaction boundaries, provider policy, and rate-limit handling.

### Modified Capabilities

None.

## Impact

- New Python application code for MTProto ingestion, storage, transcription orchestration, retrieval, and CLI commands.
- New local database schema, initially SQLite for MVP with a migration path to Postgres if concurrency or scale requires it.
- New media storage directory for downloaded Telegram files, thumbnails, extracted audio, and transcription artifacts.
- New optional integrations: Telegram MTProto client library, local or cloud speech-to-text provider, embedding model/provider, and MCP server runtime.
- Operational impact: Telegram session credentials must be stored locally and protected; sync jobs must handle Telegram `FLOOD_WAIT` and avoid aggressive scraping behavior.
