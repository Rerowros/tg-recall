## Why

Raw Telegram retrieval is accurate but costly for repeated agent work: every new task must rediscover stable facts about people, projects, decisions, and communication preferences. A private, evidence-backed memory layer lets agents reuse verified knowledge while retaining the archive as the source of truth.

## What Changes

- Add local-only wiki snapshots and Markdown pages above the Telegram archive.
- Add a delta compiler that updates pages from messages synced after the previous snapshot.
- Add compact agent lookup and source-expansion interfaces for the wiki layer.
- Preserve citations, timestamps, confidence, and revision history for every derived assertion.
- Add freshness and provenance checks for stale or unsupported wiki content.

## Capabilities

### New Capabilities

- `private-agent-wiki-memory`: Local, versioned, evidence-backed Markdown memory for people, relationships, projects, decisions, and communication styles.
- `wiki-agent-interface`: Compact wiki lookup and cited source-expansion operations for AI agents.

### Modified Capabilities

None.

## Impact

The change will add SQLite metadata for snapshots and revisions, a private profile-aware `data/profiles/<profile>/wiki/` directory, CLI/MCP interfaces, and tests. It will not send archive data to an external service or publish wiki content in source distributions.
