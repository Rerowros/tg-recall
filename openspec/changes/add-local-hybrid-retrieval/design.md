## Context

The archive has SQLite FTS and a lightweight token-overlap index. The latter is exposed as semantic search but stores tokens rather than embeddings. The next retrieval layer must remain optional, offline-capable, profile-aware, filter-consistent, and bounded for agent use.

## Goals / Non-Goals

**Goals:**

- Add genuine local semantic recall without weakening FTS or privacy defaults.
- Define a versioned vector lifecycle and deterministic hybrid ranking.
- Return compact, deduplicated evidence windows with citations and provenance.
- Degrade safely when the optional runtime or index is unavailable.

**Non-Goals:**

- Bundle a large model in the core wheel or download one automatically.
- Make remote embeddings the default.
- Replace SQLite as the source of truth or citations.

## Decisions

### Optional embedding protocol

Define a small provider protocol returning model identity, dimensions, and normalized vectors. Ship integration as an optional extra and require an explicit configured local model path/name. Model acquisition is a user action; indexing never triggers an unannounced download.

### Versioned vector records

Store source type/id, source hash, model identity, dimensions, vector encoding, and indexed timestamp. A model or source hash mismatch marks a vector stale. Raw message/transcript data remains authoritative and vector rows can be rebuilt or dropped safely.

SQLite-hosted vectors are preferred initially for portability and backup consistency. A pluggable index adapter leaves room for an optional native extension after measurement.

### Hybrid candidate fusion

Generate independently bounded FTS and vector candidate lists after scope filtering, normalize their scores, and fuse them with a documented deterministic method. Collapse duplicate message/transcript hits into one cited evidence unit and expand context only after ranking under a token budget.

### Safe fallback

`auto` uses hybrid search only when the configured index is compatible and current enough; otherwise it returns keyword/token results plus provenance explaining the fallback. Strict semantic mode can return a stable unavailable error for callers that require vectors.

## Risks / Trade-offs

- Local models increase install size and indexing time → optional extras, batch checkpoints, resource estimates, and resumable indexing.
- Score fusion can be unstable across models → version ranking configuration and test deterministic fixtures.
- Vector data can leak semantics if copied → keep it under the profile, include it only in explicit backup modes, and never publish it.

## Migration Plan

Add vector tables through the archive migration framework. Existing token rows remain usable until a user configures and explicitly builds a local embedding index. Disabling the provider leaves vectors intact but unused; an explicit maintenance command can remove them.

## Open Questions

- Select the first optional local runtime after benchmarking Python 3.13/Windows/Linux install reliability and index performance on sanitized fixtures.
