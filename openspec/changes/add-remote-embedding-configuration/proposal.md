## Why

The current genuine semantic retrieval requires an already-downloaded local
SentenceTransformers model and consumes local CPU.  A user who explicitly
accepts remote processing needs a low-cost OpenRouter embedding option without
turning network egress, profile mutation, or whole-archive indexing into an
agent side effect.

## What Changes

- Add an explicitly opted-in OpenRouter embedding provider backed by an
  environment-only API key and a small allowlist of selectable embedding
  models.
- Add human-only `config embeddings` commands to inspect choices, preview the
  privacy/cost consequences, configure a local or OpenRouter provider, and
  disable embeddings without handling credentials.
- Keep the existing scoped, resumable vector lifecycle and hybrid FTS/vector
  retrieval; make `auto` fall back to FTS when the remote provider is
  unavailable.
- Document exact data egress, model choices, cost calculation, and the
  explicit build/update workflow.

## Capabilities

### New Capabilities

- `remote-embedding-configuration`: Explicit human configuration and safe
  OpenRouter-backed embedding retrieval for selected local archive scopes.

### Modified Capabilities

- None.

## Impact

Changes affect profile configuration validation and redaction, the embedding
provider factory and CLI, provider-policy checks, tests, and Russian/English
documentation.  The feature adds a standard-library HTTPS client only; it does
not add an SDK or persist API keys in profile data.
