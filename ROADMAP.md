# Roadmap

`v0.2.0` is the current released baseline. It already provides profile-aware system storage, portable mode, scoped incremental sync and explicit backfill, content-addressed media, transcription, cited retrieval, private exports, migration, backup/restore, `doctor`, an agent guide, and a read-only MCP server.

The roadmap is ordered by dependency and risk rather than by calendar date. Every milestone keeps the existing CLI and JSON contracts compatible unless its OpenSpec change explicitly declares otherwise.

## Product principles

- Local-first by default: archive, credentials, sessions, media, wiki, and exports stay below the selected private profile.
- Explicit scope: no implicit whole-archive sync, media download, transcription, export, or provider upload.
- Safe agent boundary: agents may read, sync, materialize media, and transcribe within user-approved scope; they may not authorize Telegram, change credentials/config, purge data, or send messages.
- Evidence before synthesis: every derived result keeps stable `tg://` citations and can be expanded back to raw source context.
- Optional intelligence: embeddings and LLM providers are opt-in additions; keyword retrieval and extractive answers remain usable without them.

## Milestone 1 — v0.2.x safety and sync correctness

OpenSpec: `harden-agent-safety-and-sync`

- Enforce one agent-read policy across CLI and MCP, including metadata-only tools, result caps, and chat/date/media boundaries.
- Prevent empty incremental or backfill runs from erasing existing watermarks; make forward/backfill state monotonic and resumable.
- Add regression tests for forbidden agent commands, scoped reads, no-op sync, `FLOOD_WAIT`, and stable JSON errors.

Exit criteria: an automated agent cannot widen archive access or call forbidden operations, and repeated/no-op syncs cannot lose progress.

## Milestone 2 — v0.2.x archive maintenance

OpenSpec: `add-archive-maintenance-controls`

- Introduce explicit, ordered SQLite migrations with version checks, transactional rollback, and `doctor` diagnostics.
- Add filtered job inspection plus explicit retry, repair, and stale-job recovery controls without silently retrying permanent failures.
- Apply every advertised filter consistently to keyword, transcript, token-overlap, and future semantic retrieval.
- Add deterministic machine-readable reports for maintenance operations.

Exit criteria: upgrades are recoverable, queue failures are diagnosable and repairable, and all search modes obey the same filter contract.

## Milestone 3 — v0.3.0 agent context and knowledge orchestration

OpenSpec: `optimize-agent-context-and-memory-routing`

- Use progressive retrieval with explicit item, context, token, stage, retry, and tool-call budgets instead of repeatedly sending large chat windows.
- Save immutable cited evidence sets and compact research checkpoints so a new AI turn or Codex chat can resume prior work without rediscovering the same messages.
- Expose one profile-aware catalog over raw messages/transcripts, wiki knowledge, and saved evidence while keeping raw Telegram records authoritative.
- Generate a versioned Codex prompt that prefers available `gpt-5.3-codex-spark` for near-instant narrow read-only retrieval using its separate Codex limit, with `gpt-5.6-luna` as the stable low-cost fallback.
- Measure citations, quality, calls/turns, estimated and actual tokens where available, latency, cache reuse, and cost per successful task.

Exit criteria: repeated work can reuse current evidence, old conclusions can expand to original messages, and lower-cost routing reduces measured usage without losing required evidence or privacy boundaries.

## Milestone 4 — v0.3.x private agent wiki memory

OpenSpec: `add-private-agent-wiki-memory`

```text
raw Telegram deltas -> immutable cited snapshots -> versioned Markdown wiki -> compact agent lookup
```

- Add profile-aware snapshot, revision, assertion, and citation metadata.
- Compile only new source deltas into pages for people, relationships, projects, decisions, and communication styles.
- Separate observations from hypotheses and retain confidence, freshness, revision history, and source citations.
- Add compact lookup and raw-source expansion through safe CLI and read-only MCP interfaces.

Exit criteria: a wiki answer can always report freshness and expand each assertion to original Telegram evidence. Wiki data remains local and excluded from Git and release artifacts.

## Milestone 5 — v0.4.0 local hybrid retrieval

OpenSpec: `add-local-hybrid-retrieval`

- Replace the current token-overlap `--semantic` behavior with an explicit local embedding provider and versioned vector index.
- Combine FTS, metadata filters, transcript matches, and vector similarity with deterministic ranking and deduplicated evidence windows.
- Keep token/keyword fallback available when embeddings are disabled, unavailable, or stale.
- Expose index provenance, freshness, rebuild status, and bounded batch retrieval for agents.

Exit criteria: semantic retrieval works fully offline when configured, never weakens scope enforcement, and degrades safely to current local search.

## Milestone 6 — v0.4.x private AI export packs

OpenSpec: `add-private-ai-export-packs`

- Export bounded, profile-aware packs from raw evidence and/or synthesized wiki pages for downstream AI tools.
- Include manifests with scope, provenance, citations, freshness, schema version, and content hashes.
- Write only to the private profile exports directory by default; external paths require an explicit user choice.
- Support incremental regeneration without duplicating unchanged content.

Exit criteria: an export is portable and verifiable without containing credentials, sessions, absolute private paths, or data outside its declared scope.

## Milestone 7 — v0.5.0 policy-aware LLM answers

OpenSpec: `add-policy-aware-llm-answers`

- Add a provider adapter boundary for `ask` while keeping `extractive` as the default provider.
- Send only bounded retrieved evidence after explicit provider-policy and scope checks.
- Record provider/model, evidence citations, policy decision, token usage, and sanitized failure metadata in the local audit trail.
- Preserve deterministic JSON contracts and return cited evidence when a provider is disabled or unavailable.

Exit criteria: no provider receives archive-wide data implicitly, every synthesized answer cites its evidence, and provider failure does not block local retrieval.

## Release and OpenSpec hygiene

- Finish or supersede the six remaining tasks in `build-telegram-ai-archive-assistant`, then archive it into canonical specs.
- Keep each milestone in a separate implementation branch and archive its OpenSpec change only after focused tests, full `pytest`, distribution privacy checks, and documentation pass.
- Treat privacy regressions, destructive agent access, citation loss, and restore incompatibility as release blockers.
