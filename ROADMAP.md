# Roadmap

[English](ROADMAP.md) | [Русский](ROADMAP.ru.md)

`v0.5.0` is the current released baseline. It includes the profile-aware local archive and portable mode from `v0.2.0`, plus hardened agent safety and sync, schema v6 maintenance, budgeted agent context routing, private wiki/evidence/research memory, local hybrid retrieval, private packs, and opt-in policy-aware LLM answers.

The released milestones below preserve their version history. Future work remains ordered by dependency and risk rather than by calendar date, and keeps existing CLI and JSON contracts compatible unless a future OpenSpec change explicitly declares otherwise.

## Product principles

- Local-first by default: archive, credentials, sessions, media, wiki, and exports stay below the selected private profile.
- Explicit scope: no implicit whole-archive sync, media download, transcription, export, or provider upload.
- Safe agent boundary: agents may read, sync, materialize media, and transcribe within user-approved scope; they may not authorize Telegram, change credentials/config, purge data, or send messages.
- Evidence before synthesis: every derived result keeps stable `tg://` citations and can be expanded back to raw source context.
- Optional intelligence: embeddings and LLM providers are opt-in additions; keyword retrieval and extractive answers remain usable without them.

## Released milestone 1 — v0.2.x safety and sync correctness

OpenSpec: `harden-agent-safety-and-sync`

- Enforces one agent-read policy across CLI and MCP, including metadata-only tools, result caps, and chat/date/media boundaries.
- Preserves watermarks on empty incremental or backfill runs and keeps forward/backfill state monotonic and resumable.
- Includes regression tests for forbidden agent commands, scoped reads, no-op sync, `FLOOD_WAIT`, and stable JSON errors.

Delivered: an automated agent cannot widen archive access or call forbidden operations, and repeated/no-op syncs cannot lose progress.

## Released milestone 2 — v0.2.x archive maintenance

OpenSpec: `add-archive-maintenance-controls`

- Introduces explicit ordered SQLite migrations through schema v6, with version checks, transactional rollback, and `doctor` diagnostics.
- Adds filtered job inspection plus explicit retry, repair, and stale-job recovery controls without silently retrying permanent failures.
- Applies every advertised filter consistently to keyword, transcript, token-overlap, and semantic retrieval.
- Includes deterministic machine-readable reports for maintenance operations.

Delivered: upgrades are recoverable, queue failures are diagnosable and repairable, and all search modes obey the same filter contract.

## Released milestone 3 — v0.3.0 agent context and knowledge orchestration

OpenSpec: `optimize-agent-context-and-memory-routing`

- Uses progressive retrieval with explicit item, context, token, stage, retry, and tool-call budgets instead of repeatedly sending large chat windows.
- Saves immutable cited evidence sets and compact research checkpoints so a new AI turn or Codex chat can resume prior work without rediscovering the same messages.
- Exposes one profile-aware catalog over raw messages/transcripts, wiki knowledge, and saved evidence while keeping raw Telegram records authoritative.
- Generates a versioned Codex prompt that prefers available `gpt-5.3-codex-spark` for near-instant narrow read-only retrieval using its separate Codex limit, with `gpt-5.6-luna` as the stable low-cost fallback.
- Measures citations, quality, calls/turns, estimated and actual tokens where available, latency, cache reuse, and cost per successful task.

Delivered: repeated work can reuse current evidence, old conclusions can expand to original messages, and lower-cost routing reduces measured usage without losing required evidence or privacy boundaries.

## Released milestone 4 — v0.3.x private agent wiki memory

OpenSpec: `add-private-agent-wiki-memory`

```text
raw Telegram deltas -> immutable cited snapshots -> versioned Markdown wiki -> compact agent lookup
```

- Adds profile-aware snapshot, revision, assertion, and citation metadata.
- Compiles only new source deltas into pages for people, relationships, projects, decisions, and communication styles.
- Separates observations from hypotheses and retains confidence, freshness, revision history, and source citations.
- Adds compact lookup and raw-source expansion through safe CLI and read-only MCP interfaces.

Delivered: a wiki answer can report freshness and expand each assertion to original Telegram evidence. Wiki data remains local and excluded from Git and release artifacts.

## Released milestone 5 — v0.4.0 local hybrid retrieval

OpenSpec: `add-local-hybrid-retrieval`

- Adds an explicit local embedding provider and versioned vector index alongside the legacy token-overlap path.
- Combines FTS, metadata filters, transcript matches, and vector similarity with deterministic ranking and deduplicated evidence windows.
- Keeps token/keyword fallback available when embeddings are disabled, unavailable, or stale.
- Exposes index provenance, freshness, rebuild status, and bounded batch retrieval for agents.

Delivered: semantic retrieval works fully offline when configured, never weakens scope enforcement, and degrades safely to current local search.

## Released milestone 6 — v0.4.x private AI export packs

OpenSpec: `add-private-ai-export-packs`

- Exports bounded, profile-aware packs from raw evidence and/or synthesized wiki pages for downstream AI tools.
- Includes manifests with scope, provenance, citations, freshness, schema version, and content hashes.
- Writes only to the private profile exports directory by default; external paths require an explicit user choice.
- Supports incremental regeneration without duplicating unchanged content.

Delivered: an export is portable and verifiable without containing credentials, sessions, absolute private paths, or data outside its declared scope.

## Released milestone 7 — v0.5.0 policy-aware LLM answers

OpenSpec: `add-policy-aware-llm-answers`

- Adds a provider adapter boundary for `ask` while keeping `extractive` as the default provider.
- Sends only bounded retrieved evidence after explicit provider-policy and scope checks.
- Records provider/model, evidence citations, policy decision, token usage, and sanitized failure metadata in the local audit trail.
- Preserves deterministic JSON contracts and returns cited evidence when a provider is disabled or unavailable.

Delivered: no provider receives archive-wide data implicitly, every synthesized answer cites its evidence, and provider failure does not block local retrieval.

## Release and OpenSpec hygiene

- The listed baseline OpenSpec changes are complete and remain unarchived for release audit.
- Future milestones should use a separate implementation branch and archive their OpenSpec change only after focused tests, full `pytest`, distribution privacy checks, and documentation pass.
- Treat privacy regressions, destructive agent access, citation loss, and restore incompatibility as release blockers.
