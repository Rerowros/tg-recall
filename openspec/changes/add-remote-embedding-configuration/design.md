## Context

`SemanticConfig` currently selects only `sentence-transformers-local`; the
generic embedding protocol, SQLite vector records, source-hash freshness, and
bounded checkpoints are already provider-neutral.  `ProviderPolicy` already
has a default-deny `external_embeddings_enabled` flag, but it is not enforced
by a provider.  `config set` is human-only but is too low-level to make the
data-egress decision or the supported OpenRouter model choices clear.

## Goals / Non-Goals

**Goals:**

- Offer a clear human-only embedding setup flow with disabled, local, and
  OpenRouter choices.
- Keep selected archive text and retrieved vectors local except for the exact
  batch/query submitted to an explicitly enabled remote provider.
- Support a curated OpenRouter model catalog, deterministic model identities,
  bounded/resumable indexing, and FTS fallback.
- Keep API keys out of profile JSON, credentials files, JSON output, and logs.

**Non-Goals:**

- Background or agent-triggered remote indexing, automatic model downloads,
  automatic whole-archive indexing, or dynamic remote model discovery.
- Sending media binaries, Telegram sessions, credentials, profile paths, wiki,
  or complete archives to OpenRouter.
- Removing local embeddings or replacing FTS.

## Decisions

### Add a dedicated human configuration surface

`tg-recall config embeddings choices` prints the stable provider/model catalog,
data-egress notice, and price unit. `config embeddings setup` accepts explicit
flags for disabled/local/OpenRouter so it can be used non-interactively by a
human, and prompts only when attached to a TTY. OpenRouter setup requires an
exact confirmation flag acknowledging that selected message/transcript text is
sent to the provider; it enables `external_embeddings_enabled`. The command
never reads or writes an API key.

This is clearer and safer than requiring a user to discover a collection of
`config set` paths. `config set` remains compatible for existing callers.

### Curate OpenRouter models and use an environment-only key

Ship a small catalog containing `perplexity/pplx-embed-v1-0.6b`,
`perplexity/pplx-embed-v1-4b`, and `voyageai/voyage-4-lite`; configuration
accepts only catalog IDs. The provider reads `OPENROUTER_API_KEY` at runtime.
It calls the OpenRouter embeddings HTTPS endpoint using the standard library,
with short timeouts and bounded batches; no SDK is required.

The initial provider metadata probe contains only a fixed non-archive sentinel
and discovers the vector dimension from the API response. The resulting model
identity hashes the provider protocol, catalog model ID, and returned
dimension, so incompatible vectors are not mixed. This avoids hard-coding a
dimension that can drift. A custom arbitrary model ID was rejected because
model capability, price, and response compatibility would be unvalidated.

### Retain explicit scoped indexing and local hybrid retrieval

`index embeddings build/rebuild` remains the sole path that sends selected
archive text in bounded batches; it already requires one `--chat-id` and keeps
checkpoint/source-hash semantics. Retrieval embeds only the query remotely,
then searches and fuses candidates in local SQLite. `auto` falls back to FTS
on missing key, denied policy, timeout, or malformed provider output; strict
semantic/hybrid requests return the existing stable unavailable error.

Automatic reindexing after sync was rejected: it would create unexpected
external egress and conflicts with local-first defaults.

## Risks / Trade-offs

- [Archive text leaves the device] → require explicit human confirmation,
  default-deny policy, selected-chat build, and clear documentation.
- [Provider outage/rate limits] → timeout, stable unavailable reason, and FTS
  fallback in automatic mode; completed checkpoints remain reusable.
- [Remote model behavior changes] → pin catalog IDs, discover dimensions from
  response, and include protocol/model/dimension in the model identity.
- [Low-cost model has weak Russian recall] → expose model choice and document
  a small cited evaluation before changing the configured model.

## Migration Plan

Existing profiles retain `semantic.provider=local-token` and no network
behavior. Loading old profile JSON supplies defaults for new semantic fields.
Disabling the feature retains vectors but removes them from use; switching
models naturally treats previous vectors as incompatible. Rollback is setting
the provider to disabled or unsetting `OPENROUTER_API_KEY`; no archive data is
deleted.

## Open Questions

- None; model prices are displayed as catalog guidance only and are not used
  for billing or cost enforcement.
