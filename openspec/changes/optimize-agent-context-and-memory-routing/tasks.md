## 1. Baseline And Evaluation

- [x] 1.1 Create sanitized representative tasks for fresh lookup, repeated lookup, old-message expansion, conflicting evidence, and resumed research.
- [x] 1.2 Capture baseline result quality, citation coverage, retrieval calls/stages, returned context, estimated tokens, latency, and provider-reported usage when available.
- [x] 1.3 Define pass/fail thresholds that reject token or turn savings when required evidence quality regresses.

## 2. Context Budgeting And Evidence Reuse

- [x] 2.1 Add migrations and storage repositories for immutable evidence sets, members, source versions, and research sessions.
- [x] 2.2 Define `token_budget` as a serialized retrieval-payload cap and add the versioned `TokenCounter` interface.
- [x] 2.3 Benchmark exact tokenizer candidates and calibrate the conservative fallback on sanitized Russian/English/emoji/URL/transcript/JSON fixtures.
- [x] 2.4 Implement a 15% initial safety margin and report budget, counter, estimate, margin, actual-usage provenance, and truncation fields.
- [x] 2.5 Implement progressive retrieval stages with explicit item, context, token, stage, retry, and supported tool-call budgets.
- [x] 2.6 Deduplicate overlapping message/transcript windows before exact serialization and budget accounting.
- [x] 2.7 Reuse current evidence sets and selectively mark/refresh stale members after source or wiki revision changes.
- [x] 2.8 Add stable JSON results for complete, incomplete, stale, reused, and budget-exhausted outcomes.

## 3. Unified Knowledge Interface

- [x] 3.1 Add a profile-aware catalog over raw messages/transcripts, wiki pages/assertions, and saved evidence sets without duplicating raw content.
- [x] 3.2 Return layer, authority, confidence, freshness, source version, and citations for every catalog hit.
- [x] 3.3 Add research-session create, checkpoint, list, inspect, resume, and selective refresh commands.
- [x] 3.4 Add bounded old-source expansion from wiki/session/evidence references through the shared access policy.
- [x] 3.5 Extend read-only MCP with compact catalog query, session inspection, and cited source expansion only.

## 4. Codex Routing Guide

- [x] 4.1 Define a versioned structured `agent guide` schema for capabilities, budgets, routing preferences, escalation, fallbacks, and safety boundaries.
- [x] 4.2 Generate human and JSON Codex prompts from the same schema while preserving existing response fields.
- [x] 4.3 Prefer available `gpt-5.3-codex-spark` for near-instant bounded read-only search and use `gpt-5.6-luna` low/medium as the stable low-cost fallback.
- [x] 4.4 Add current-model fallback, evidence-based escalation, and one-subagent-by-default guidance without editing Codex configuration.
- [x] 4.5 Document the current v0.2-compatible prompt and the planned knowledge/session additions separately.

## 5. Usage Telemetry And Privacy

- [x] 5.1 Record session-local retrieval stages, calls, items, deduplication, retries, latency, estimates, evidence reuse, and sufficiency.
- [x] 5.2 Store host/provider actual token fields separately from estimates with model and source provenance.
- [x] 5.3 Apply profile isolation, secret redaction, package/Git exclusion, audit minimization, and backup policy to all new state.
- [x] 5.4 Add tests proving that full Codex transcripts, hidden reasoning, credentials, sessions, and cross-profile records are never persisted.

## 6. Validation

- [x] 6.1 Run the baseline and optimized configurations on every representative task and report quality, citations, calls/turns, tokens, latency, and cost per successful task.
- [x] 6.2 Run focused context/routing/knowledge/MCP tests, full pytest, distribution privacy and backup/restore tests, and strict OpenSpec validation.
