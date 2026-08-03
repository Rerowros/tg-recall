## Why

Repeated AI work over Telegram currently spends turns and context rediscovering the same messages, resending overlapping evidence, and using a stronger model for retrieval that a smaller Codex subagent can handle. `tg-recall` needs an explicit context-budgeting and knowledge-access contract so agents can resume prior work, expand old sources only when needed, and route narrow search to lower-cost models without making model availability a hard dependency.

## What Changes

- Add progressive retrieval that starts with compact knowledge/wiki hits, widens to bounded archive evidence, and expands full message context only on demand.
- Add immutable saved evidence sets and resumable local research sessions containing queries, scope, citations, source versions, summaries, and budgets rather than copied chat history.
- Add a unified profile-aware knowledge catalog over raw messages/transcripts, synthesized wiki pages, and saved evidence sets while preserving raw Telegram records as the source of truth.
- Add per-request context budgets and usage telemetry for turns, tool calls, retrieved items, estimated tokens, cache reuse, retries, and provider-reported usage when available.
- Add a versioned Codex prompt from `agent guide` that prefers `gpt-5.3-codex-spark` for near-instant narrow read-only retrieval when its separate Codex usage limit is available, with `gpt-5.6-luna` as the stable low-cost fallback for narrow search/extraction.
- Require graceful routing fallback to an available model and escalation to a stronger model only for ambiguous synthesis, security-critical review, or failed low-cost retrieval.
- Keep MCP read-only and keep all knowledge/session state below the selected private profile.

## Capabilities

### New Capabilities

- `agent-context-budgeting`: Progressive retrieval, context/turn budgets, evidence reuse, telemetry, and measurable optimization gates.
- `codex-model-routing`: Versioned availability-aware Codex prompt and cost-aware routing policy for retrieval subagents.
- `unified-agent-knowledge`: One local catalog and resumable session interface over raw archive evidence, wiki memory, and saved evidence sets.

### Modified Capabilities

None. Related wiki, export, and agent-safety capabilities are still active changes and are treated as dependencies rather than duplicated here.

## Impact

The change affects assistant/retrieval orchestration, SQLite migrations, profile-local state paths, `agent guide`, CLI and read-only MCP query schemas, audit/usage reporting, documentation, and evaluation fixtures. It adds no required cloud provider, does not configure a user's Codex installation, and does not store Codex private reasoning or full conversation transcripts as knowledge.
