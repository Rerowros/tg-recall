## ADDED Requirements

### Requirement: Human-only embedding setup choices
The system SHALL provide a human-only `config embeddings` surface which lists
disabled, local, and supported OpenRouter choices without changing a profile.
It SHALL require an explicit acknowledgement before configuring a remote
provider and SHALL reject the setup command from an automation shell.

#### Scenario: User inspects choices
- **WHEN** a human runs `config embeddings choices`
- **THEN** the command returns the supported provider/model IDs, price units,
  and an explicit statement that OpenRouter receives selected text and queries
  only after remote setup

#### Scenario: User configures a remote provider
- **WHEN** a human selects a catalog OpenRouter model and supplies the exact
  remote-text acknowledgement
- **THEN** the selected profile enables semantic retrieval and external
  embeddings without storing an API key

#### Scenario: Agent attempts embedding setup
- **WHEN** an AI or automation shell invokes `config embeddings setup`
- **THEN** the command returns the existing human-only forbidden error before
  changing configuration or making a network request

### Requirement: Explicit remote embedding boundary
The system SHALL instantiate OpenRouter embeddings only when the selected
profile has both the OpenRouter provider and `external_embeddings_enabled`.
It SHALL read `OPENROUTER_API_KEY` only at request time and SHALL NOT include
the key in configuration, diagnostics, JSON output, logs, or exceptions.

#### Scenario: Missing explicit provider policy
- **WHEN** a profile selects OpenRouter but external embeddings are not
  explicitly enabled
- **THEN** no archive text is sent and automatic retrieval reports an embedding
  provider unavailable fallback

#### Scenario: Missing environment key
- **WHEN** a remote-enabled profile has no `OPENROUTER_API_KEY`
- **THEN** no request is made and automatic retrieval falls back to FTS

### Requirement: Curated remote model and compatible vectors
The system SHALL accept only documented OpenRouter embedding model IDs and
shall associate every remote vector with the provider protocol, selected model,
returned dimension, and source hash. It SHALL NOT mix vectors produced by
incompatible model identities.

#### Scenario: Unsupported model ID
- **WHEN** a user passes a model ID outside the catalog
- **THEN** setup rejects it before saving configuration or making a request

#### Scenario: Model dimension is discovered
- **WHEN** a valid remote provider first needs metadata
- **THEN** it sends only a fixed non-archive probe, records the returned vector
  dimension in its local model identity, and later rejects incompatible output

### Requirement: Scoped remote indexing and local retrieval
The system SHALL reuse bounded, selected-chat embedding index commands for
remote providers. It SHALL send only each selected source text batch to the
remote API, persist vectors/checkpoints locally, and execute vector search and
hybrid ranking locally. It SHALL NOT automatically index after sync.

#### Scenario: Build selected remote batches
- **WHEN** a human explicitly builds embeddings for one chat using a
  remote-enabled profile
- **THEN** only missing or stale records from that selected scope are submitted
  in bounded batches and successful batches advance the local checkpoint

#### Scenario: Remote provider is unreachable in automatic retrieval
- **WHEN** remote query embedding times out or returns an invalid response
- **THEN** `auto` returns bounded FTS evidence with an observable fallback
  reason and strict semantic retrieval returns `semantic_unavailable`
