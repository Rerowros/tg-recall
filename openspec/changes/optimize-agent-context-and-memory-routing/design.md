## Context

`tg-recall retrieve` already bounds result count, context radius, and approximate token budget, while the planned wiki adds compact synthesized memory. What is missing is orchestration across turns: agents can repeat searches, resend the same windows, lose prior evidence when a chat changes, or delegate simple lookup to an unnecessarily expensive model. Codex has its own optional local memories, but those are generated product state and cannot replace an evidence-backed Telegram knowledge base.

The Codex model catalog is surface- and account-dependent. Current official guidance positions `gpt-5.6-luna` for fast, narrow, high-volume work. `gpt-5.3-codex-spark` is a fast, less-capable research-preview Codex model for Pro subscribers; it runs on specialized low-latency hardware and uses a separate usage limit that can vary with demand. The project can publish routing preferences, but it cannot guarantee entitlement, remaining allowance, or mutate the user's global Codex configuration.

## Goals / Non-Goals

**Goals:**

- Reduce repeated turns, tool calls, context size, and model cost without losing citations or answer quality.
- Let an agent resume a prior Telegram research task from compact local state and expand old source messages only when required.
- Give Codex a versioned, copy-ready and machine-readable routing prompt with availability-safe fallbacks.
- Keep raw archive records authoritative while presenting wiki, prior evidence, and fresh retrieval through one interface.
- Measure optimization using representative tasks rather than assuming fewer tokens always means a better result.

**Non-Goals:**

- Control Codex billing, entitlements, global configuration, or exact model availability from `tg-recall`.
- Persist hidden model reasoning, full Codex transcripts, credentials, sessions, or unrelated project context.
- Replace raw Telegram messages with summaries or make Codex memories the archive source of truth.
- Add automatic whole-archive sync, wiki compilation, export, or cloud upload.

## Decisions

### Progressive retrieval ladder

Use a fixed retrieval ladder and stop at the first sufficient evidence set:

```text
saved session/evidence set
  -> compact current wiki/catalog hits
  -> narrow raw message/transcript retrieval
  -> cited context expansion
  -> explicitly requested media/export
```

The default Codex prompt starts with a small request such as `--limit 8 --context 2 --token-budget 4000`, permits one measured widening when evidence is incomplete, and avoids JSONL export unless the task genuinely needs long sequential analysis. This is preferable to retrieving a large maximum context once because most questions need only a few source windows.

### Immutable evidence sets and resumable research sessions

Add profile-local metadata for `research_sessions`, `evidence_sets`, and cited members. An evidence set records normalized scope, query/purpose, source IDs and versions, retrieval mode, budgets, compact summary, and creation time. It stores references and short derived notes, not duplicate full archive rows.

A resumed session first loads its last compact checkpoint and validates source versions. Current members can be reused without another search. Stale members remain visible with a stale marker and can be refreshed selectively. Sessions are never shared between profiles.

### Unified knowledge catalog, layered authority

Expose one query surface over three layers:

1. Raw messages/transcripts: authoritative evidence.
2. Versioned wiki assertions/pages: compact derived knowledge with freshness and citations.
3. Saved evidence sets/research checkpoints: task-specific navigation and prior conclusions.

The catalog stores stable logical references and source-version metadata rather than copying content into a fourth knowledge store. Every derived record resolves to `tg://` citations or another declared raw source. Codex memories may help recall that `tg-recall` exists, but the generated prompt tells agents to verify Telegram facts through this catalog.

### Availability-aware Codex model routing

Publish routing as preference plus fallback, not a hard-coded requirement:

- Preferred near-instant read-only search: `gpt-5.3-codex-spark` when the current Codex surface/account exposes it, its separate usage limit is not exhausted, and preview-model quality is acceptable for the bounded task.
- Stable narrow-search fallback and high-volume extraction/classification: `gpt-5.6-luna` at low or medium reasoning.
- Ambiguous multi-source synthesis: keep the parent model or escalate to `gpt-5.6-terra`.
- Security-critical, destructive, migration, or difficult correctness review: use the configured stronger reviewer/parent model.
- If spawning or the preferred model is unavailable, continue with the current model under the same scope/budget; never fail archive retrieval solely because routing failed.

The prompt limits delegation to one bounded search agent by default and allows parallel agents only for genuinely independent scopes. This avoids spending more tokens on subagents than a single direct retrieval would use.

### Versioned agent prompt and capability discovery

Extend `agent guide` additively with a stable prompt version, supported command/capability list, recommended budgets, routing preferences, escalation rules, and safety boundaries. Human text and JSON share the same structured source. Existing JSON fields remain compatible; new fields are additive.

The project documentation contains a copy-ready prompt, while runtime output reflects the installed `tg-recall` version. Neither surface edits `.codex/config.toml` or custom agent TOMLs.

### Token budget semantics and counting

`--token-budget` remains backward compatible but is defined precisely as the maximum estimated serialized retrieval payload returned by `tg-recall`, not the total Codex thread context, hidden reasoning, output, or billed usage.

Introduce a `TokenCounter` boundary:

1. Use an exact model/provider tokenizer only when the selected tokenizer and version are known and locally available.
2. Otherwise use a conservative calibrated estimator over the final UTF-8/JSON payload, not the current `len(text) + 100` heuristic.
3. Calibrate the fallback on sanitized Russian, English, emoji, URLs, Telegram citations, transcript text, and JSON metadata; store estimator name/version and measured error.
4. Reserve 15% of the requested budget for serialization variance and wrapper metadata until evaluation supports a smaller margin.
5. Deduplicate first, serialize the candidate item exactly as returned, count it, then add it only if it fits. Never drop the citation or split metadata from included text; truncate an oversized first evidence body explicitly when necessary.

Return `token_budget`, `usable_payload_budget`, `estimated_tokens`, `counter`, `counter_version`, `safety_margin`, and `truncated`. If the host later supplies actual model usage, keep it separate because it includes context outside the retrieval payload.

### Usage telemetry distinguishes estimates from actuals

Record per research session: retrieval stages, `tg-recall` calls, returned items/windows, deduplicated items, estimated input tokens, reused evidence, cache hits, retries, elapsed time, and result sufficiency. If a host/provider supplies actual model usage, store it in separate explicitly labeled fields. Never present character-based estimates as billed Codex tokens or credits.

### Evaluation gate

Build sanitized representative tasks and compare baseline versus optimized orchestration on answer correctness, citation coverage, stale-evidence handling, input/output tokens when available, estimated context, turns/tool calls, latency, and cost per successful task. A lower token count is accepted only when required evidence and answer quality remain intact.

## Risks / Trade-offs

- Compact checkpoints can preserve a wrong conclusion → label them as derived, keep source citations, and revalidate freshness before reuse.
- Small first-pass budgets can miss evidence → allow one bounded widening and make insufficiency explicit instead of fabricating an answer.
- Spark availability or its separate allowance can change → capability-check selection and keep Luna/current-model fallback; do not convert the separate limit into an invented per-token credit price.
- Session telemetry can reveal sensitive research intent → keep it profile-local, exclude it from logs/packages, and apply the same backup/privacy rules as wiki data.
- A unified interface can blur source authority → return layer, freshness, confidence, and citation metadata for every result.

## Migration Plan

Add the new tables through the archive maintenance migration framework. Existing archives require no content rewrite. Introduce the structured guide and knowledge query additively, then enable evidence-set persistence and session resume. If rolled back, the raw archive/wiki remain valid and the new derived session tables can be ignored.

## Open Questions

- Whether a future Codex plugin should consume the structured guide directly; the first implementation remains CLI/MCP compatible and requires no plugin.
- Which exact local tokenizer implementation covers the current Codex models; implementation must benchmark candidates and retain the calibrated conservative fallback when an exact mapping is unavailable.
