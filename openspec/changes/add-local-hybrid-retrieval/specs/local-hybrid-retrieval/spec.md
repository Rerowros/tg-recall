## ADDED Requirements

### Requirement: Explicit local embedding provider
The system SHALL use embeddings only when a local provider and model are explicitly configured and SHALL NOT automatically download a model or send archive text to a remote service.

#### Scenario: No embedding provider configured
- **WHEN** the user searches without a configured local embedding provider
- **THEN** keyword and token-based retrieval remain available without an installation or network error

#### Scenario: Local provider configured
- **WHEN** the user explicitly configures an available local model and starts indexing
- **THEN** embeddings are computed locally for only the selected profile and scope

### Requirement: Versioned vector lifecycle
The system SHALL associate each vector with source identity/hash, model identity, dimensions, and index timestamp and SHALL detect stale or incompatible vectors.

#### Scenario: Source transcript changes
- **WHEN** a transcript source hash differs from its stored vector metadata
- **THEN** the vector is marked stale and excluded from strict semantic results until reindexed

#### Scenario: Model changes
- **WHEN** the configured model identity or dimensions differ from stored vectors
- **THEN** the system reports an incompatible index and does not mix vectors from both models

### Requirement: Scope-safe hybrid ranking
The system SHALL apply all access and metadata filters before fusing FTS and vector candidates.

#### Scenario: Hybrid search in one allowed chat
- **WHEN** an agent runs hybrid retrieval for one allowed chat and date range
- **THEN** every keyword and vector candidate is within that intersection before scoring

### Requirement: Deduplicated bounded evidence
The system SHALL merge duplicate message and transcript hits into deterministic cited evidence windows bounded by result and token budgets.

#### Scenario: Message and transcript match the same citation
- **WHEN** both source text and a linked transcript rank for the same Telegram message
- **THEN** retrieval returns one evidence window with the citation and linked provenance rather than duplicate context

### Requirement: Observable fallback
The system SHALL report retrieval mode, model/index provenance, freshness, and fallback reason in machine-readable results.

#### Scenario: Hybrid index unavailable in automatic mode
- **WHEN** automatic retrieval cannot use a current compatible vector index
- **THEN** it returns local keyword/token evidence and marks the fallback reason without failing the request

#### Scenario: Strict semantic mode unavailable
- **WHEN** a caller explicitly requires semantic vectors and no compatible index exists
- **THEN** the system returns a stable unavailable error and does not label token overlap as vector search

### Requirement: Resumable local indexing
The system SHALL build embeddings in bounded checkpoints and resume without recomputing unchanged successful records.

#### Scenario: Index build is interrupted
- **WHEN** local embedding generation stops after a completed checkpoint
- **THEN** the next run resumes from remaining stale or missing records within the same model version
