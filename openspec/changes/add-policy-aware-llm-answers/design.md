## Context

`ArchiveAssistant.answer()` is extractive by default and blocks any configured provider after checking policy. Retrieval, citations, agent boundaries, and export/wiki provenance are the prerequisites for safe synthesis. The provider layer must stay optional and must never receive archive-wide or out-of-policy data.

## Goals / Non-Goals

**Goals:**

- Add a minimal provider protocol over bounded cited evidence.
- Keep extractive behavior as the default and dependable fallback.
- Preserve citation traceability and stable JSON results.
- Audit provider usage without logging secrets or full private prompts.

**Non-Goals:**

- Autonomous Telegram actions, message sending, or tool loops.
- Provider-side memory, training uploads, or background archive analysis.
- Requiring a cloud SDK in the core installation.

## Decisions

### Retrieval remains authoritative

The assistant first obtains an authorized `EvidenceContext` with immutable citation IDs and budgets. Provider adapters accept a serialized bounded context plus answer schema; they do not receive database handles, profile paths, Telegram credentials, or discovery tools.

### Explicit provider policy gate

A call requires a non-extractive configured provider, explicit external-LLM enablement, allowed data classes, resolved scope, credentials, and model selection. The gate runs before prompt construction. Policy denials do not attempt a network connection.

### Structured cited answer

Adapters return answer text plus referenced evidence IDs and usage metadata. The core validates references against the supplied context. Unknown citations or a wholly uncited answer fail validation and trigger a local evidence fallback rather than being presented as verified.

### Optional adapters and safe audit

Provider implementations live behind optional extras. Audit stores provider/model, evidence IDs/count, policy result, token/latency metrics, and sanitized error class. Full prompt/response retention is off by default and cannot be enabled by an agent command.

## Risks / Trade-offs

- Providers can retain submitted evidence → require explicit policy/documentation and keep the default local extractive path.
- Model-generated citations can be wrong → validate citation IDs against the exact evidence set and clearly label synthesis.
- Fallback can hide outages → return provider status alongside cited local evidence in JSON.

## Migration Plan

No existing configuration changes behavior: `extractive` remains the default. Add provider configuration fields compatibly, implement a mock adapter and contract tests, then one optional real adapter. Removing the extra returns the application to extractive behavior.

## Open Questions

- Select the first real provider only after confirming current API privacy controls, structured-output support, and Python 3.13 compatibility during implementation.
