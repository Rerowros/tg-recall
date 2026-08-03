## Why

The current `--semantic` mode uses local token overlap, so it cannot reliably find conceptually related evidence with different wording. A fully local, optional hybrid retrieval layer can improve recall without making cloud processing or embeddings a prerequisite.

## What Changes

- Add an embedding-provider protocol and one explicit local provider integration.
- Store versioned vector records with model, dimensions, source hash, and freshness metadata.
- Combine FTS, metadata, transcript, and vector results using deterministic ranking and deduplicated context windows.
- Apply the same scope and filter contract to every retrieval stage.
- Fall back to existing keyword/token retrieval when embeddings are disabled, unavailable, incompatible, or stale.
- Add index status/rebuild reporting and bounded batch retrieval for agent workflows.

## Capabilities

### New Capabilities

- `local-hybrid-retrieval`: Covers opt-in local embeddings, vector lifecycle, hybrid ranking, fallback, and bounded evidence batches.

### Modified Capabilities

None. The canonical specs have not yet been archived.

## Impact

The change affects configuration, SQLite schema/migrations, indexing, retrieval, CLI/MCP read APIs, `doctor`, and tests. The local embedding runtime must be optional, and no archive content may leave the machine unless a separate provider policy explicitly permits it.
