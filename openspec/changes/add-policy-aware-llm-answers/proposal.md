## Why

`ask` currently produces a safe extractive answer and intentionally rejects configured LLM providers. Adding synthesis is useful only after retrieval and scope controls are stable, and it must not turn provider configuration into implicit archive upload.

## What Changes

- Add a provider adapter boundary for synthesized `ask` responses while keeping `extractive` as the default.
- Require explicit external-LLM policy, selected scope, bounded evidence, and configured credentials before any provider call.
- Preserve citations in prompts and responses and reject uncited synthesized claims from the structured result.
- Audit provider/model, policy decision, evidence IDs, token usage, latency, and sanitized failures without storing secrets or full prompts by default.
- Return local cited evidence when the provider is disabled or unavailable.
- Keep MCP read-only and do not add Telegram message sending or autonomous actions.

## Capabilities

### New Capabilities

- `policy-aware-llm-answers`: Covers opt-in provider synthesis over bounded cited evidence, fallback, audit, and stable answer contracts.

### Modified Capabilities

None. The canonical specs have not yet been archived.

## Impact

The change affects assistant/provider interfaces, configuration and credentials, CLI/MCP read responses, audit events, documentation, and mocked provider tests. Provider SDKs, if any, must be optional extras rather than required core dependencies.
