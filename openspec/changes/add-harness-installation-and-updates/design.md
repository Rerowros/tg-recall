## Context

`tg-recall` v0.5.0 is installed from local/release artifacts, exposes the stdio `tg-recall-mcp` server, and publishes a structured `AgentGuide`. It does not currently discover newer releases, update itself, or install MCP and persistent instructions into an AI harness. The README uses `uv tool install --editable .`, PyPI has no `tg-recall` project, and GitHub Releases are therefore the stable distribution source today.

The change crosses CLI dispatch, platform directories, release networking, subprocess execution, configuration parsing, atomic filesystem mutation, MCP initialization, and four harness formats. It must preserve the local-first privacy boundary: no archive or profile state participates in update or integration lifecycle operations, MCP remains read-only, and an AI shell cannot use these commands to alter its own policy.

## Goals / Non-Goals

**Goals:**

- Provide a trustworthy install, preview, status, refresh, and uninstall lifecycle for Codex, Claude Code, Cursor, and explicit generic destinations.
- Keep one versioned guide as the semantic source for all installed instructions.
- Provide bounded GitHub release discovery, optional human-enabled periodic notices, and a verified update path for supported `uv tool` installations.
- Preserve unrelated harness configuration byte-for-byte where practical and semantically in structured JSON/TOML sections.
- Fail closed on malformed files, ambiguous ownership, unsupported scopes, path escapes, or unknown installation provenance.
- Produce stable JSON suitable for installers and support scripts without exposing secrets or Telegram data.

**Non-Goals:**

- Publishing to PyPI in this change.
- Silently enabling update checks or silently applying an update.
- Editing undocumented Cursor user settings, Claude internal state, Codex model entitlements, or custom-agent model configuration.
- Installing Codex/Claude/Cursor themselves.
- Storing full harness conversations, hidden reasoning, credentials, archive content, or profile-specific paths in integration state.
- Adding any mutating MCP tool or allowing agents to invoke lifecycle commands.

## Decisions

### Use two explicit human lifecycle namespaces

The CLI adds:

```text
tg-recall update check|status|configure|apply
tg-recall integrate list|preview|install|status|refresh|uninstall
```

Integration commands accept repeatable `--target codex|claude-code|cursor|generic` (or `all`), `--scope user|project`, and `--project-root` for project scope. Generic mode additionally requires explicit MCP and instruction destinations below an explicit output root. `preview` computes the complete result without writes; `install` applies new owned content; `refresh` only updates already owned content; `uninstall` only removes owned content.

Every `update` and `integrate` subcommand is pre-classified human-only before `load_config()`. In an AI/CI/automation shell, dispatch returns the stable policy error before credentials, profiles, harness files, network, subprocess, or SQLite are touched. The existing trusted-automation confirmation is intentionally not an override for self-modifying harness configuration.

This is preferred to putting installation below `agent`: the current agent contract explicitly forbids configuration mutation, and a separate namespace makes the human boundary visible.

### Build all instructions from `AgentGuide`

`AgentGuide` remains the source of semantic fields and human rendering. The hard-coded `build_agent_guide("0.2.0")`, `current_v0_2`, and obsolete planned-v0.2 wording are removed in favor of `tg_recall.__version__` and version-neutral keys. Existing top-level JSON fields remain additive where possible; the obsolete field is removed only because it is demonstrably incorrect in the current release and the new integration schema is versioned separately.

Harness renderers consume the guide object rather than README prose. Each artifact includes a small generated header with guide schema, prompt version, package version, and SHA-256 content digest. The instruction tells agents to use bounded cited retrieval, prefer Spark/Luna only when available, treat local summaries as navigation rather than source truth, and never invoke update/integrate/auth/purge/configuration/message-sending operations.

MCP initialization includes a concise `instructions` value generated from the same guide. It is advisory for clients that support the protocol field; the tools list remains the existing static read-only allowlist.

### Use adapters with an honest capability matrix

Each adapter declares supported components by scope and returns `installed`, `updated`, `unchanged`, `partial`, `manual_required`, `conflict`, or `unsupported` per component.

| Harness | Project instructions | User instructions | Project MCP | User MCP |
|---|---|---|---|---|
| Codex | managed block in `AGENTS.md` | managed block in documented `~/.codex/AGENTS.md` | managed TOML block when project config is supported | managed block in `~/.codex/config.toml` |
| Claude Code | managed block in `CLAUDE.md` | managed `~/.claude/CLAUDE.md` | strict merge in `.mcp.json` | documented Claude CLI when available, otherwise manual |
| Cursor | owned `.cursor/rules/tg-recall.mdc` | manual User Rules step | strict merge in `.cursor/mcp.json` | strict merge in `~/.cursor/mcp.json` |
| Generic | explicit instruction destination | explicit instruction destination | explicit MCP destination | explicit MCP destination |

The adapter never guesses undocumented paths. Missing client executables or unsupported instruction storage produce `partial` plus a copy-ready action, not false success.

### Own the smallest possible configuration fragment

Markdown uses exactly one begin/end marker containing owner, schema, guide version, and digest. Cursor uses a dedicated owned `.mdc` file. JSON is parsed with duplicate-key rejection and only `mcpServers["tg-recall"]` is merged. TOML uses a clearly delimited owned block for `[mcp_servers.tg-recall]`; an equivalent unowned entry is accepted as `unchanged`, but a different unowned entry is a conflict.

Malformed JSON/TOML, duplicate/unclosed markers, an edited owned block whose digest no longer matches, or a conflicting `tg-recall` entry causes a no-write conflict. Refresh replaces only a valid owned fragment. Uninstall deletes only a valid owned fragment and does not delete a file that still contains unrelated content.

Before a real change, the system validates the canonical target boundary, rejects symlinks/junctions/reparse points for targets and writable parents, computes all output, writes a private sibling backup of an existing file, writes a private sibling temporary file, rechecks the parent, then atomically replaces the target. Backups contain only the harness file and a public hash manifest, never Telegram state. A backup is not created for preview or no-op.

This approach is preferred to invoking arbitrary shell snippets or rewriting whole instruction files because ownership and rollback remain inspectable.

### Store integration metadata outside profiles

Minimal ownership records live below the global application state root, or the portable root when `--home`/`TG_RECALL_HOME` is selected. Records contain harness, scope, canonical target, component, guide version, digest, timestamps, and backup reference. They contain no profile name, archive path, MCP arguments with secrets, or copied file body. Markers/config content remain sufficient for status if state is missing.

### Use GitHub Releases as the current update source

`update check` calls the fixed HTTPS endpoint for `Rerowros/tg-recall`, with a short timeout, bounded response size, explicit media type, no cookies/authentication, and a non-unique user agent. It accepts only a stable `vMAJOR.MINOR.PATCH` release and an exact wheel name and GitHub release download path. Cache entries below the global application cache contain only public release metadata, ETag, check time, and status.

Periodic checks default to off. `update configure --interval-hours N` is an explicit human opt-in. When enabled, only eligible interactive CLI runs may perform an if-due best-effort check; help, MCP, AI/CI shells, and commands with no handler never check. A failed periodic check never changes the requested command exit status.

### Detect provenance and verify before update

The updater reads installed distribution metadata and `direct_url.json`, locates `uv`, and confirms that the current executable belongs to a supported non-editable uv tool environment. Editable, system package, unknown, and mismatched installations return `manual_required` with an argument-vector command but do not mutate anything.

For a supported update, the wheel is downloaded to the global update cache with a size bound, its SHA-256 is matched against the GitHub asset digest, its filename/version are revalidated, and `uv tool install --force <verified-local-wheel>` is invoked with `shell=False`. Remote tag text, asset names, URLs, and local metadata are never interpolated into a shell command. Staged data is removed on validation failure. The updater reports success only on a zero subprocess exit and tells the human to run `tg-recall integrate refresh`; it does not refresh harness files using code that may have just been replaced.

### Version lifecycle JSON independently

Both namespaces return one JSON document with `schema_version`, `action`, `status`, installed version, deterministic target/component results, changed and backup paths, conflicts, warnings, manual actions, and next actions. Human text is derived from the same result. Paths are limited to selected harness configuration; archive and credential paths are never serialized.

## Risks / Trade-offs

- **[Client formats change]** → Keep adapters isolated, expose partial/manual states, and test against documented fixtures rather than undocumented client databases.
- **[Self-update can fail on a locked Windows tool environment]** → Preserve the verified wheel, report the exact retry command, and never claim the installed version changed without a successful installer result.
- **[TOML comments are hard to preserve with a full parser]** → Own one delimited table block, refuse conflicts, and avoid reserializing unrelated TOML.
- **[Opt-in periodic checks add network latency]** → Use cache/ETag, a short timeout, best-effort post-command behavior, and no effect on the main command result.
- **[User edits a managed block]** → Treat digest mismatch as a conflict; preview explains the difference and uninstall never discards user edits.
- **[Backups could contain unrelated harness secrets]** → Store them privately beside the selected configuration, list them explicitly, never copy them into tg-recall profile/archive backups, and never include their contents in output.

## Migration Plan

1. Add guide version fixes and rendering snapshots without changing MCP tools.
2. Add filesystem ownership primitives and adapter tests using temporary homes/projects.
3. Add integration CLI and pre-dispatch human gate.
4. Add release check/cache/provenance/apply behind explicit commands; keep periodic checks off.
5. Update installation documentation to use signed/digested GitHub release assets and document editable-development behavior.
6. Validate distributions contain code/docs only and no generated harness configuration, archive, credentials, profiles, or integration backups.

Rollback is code-only: uninstall owned harness fragments with the installed lifecycle command or restore the explicitly reported adjacent backup, then downgrade the package from a prior verified release wheel. No Telegram schema or archive migration is introduced.

## Open Questions

- Whether a later release should publish to PyPI so standard `uv tool upgrade tg-recall` can replace the GitHub-wheel updater.
- Whether Codex and Claude user-scope instruction paths should migrate to native shared Skills after their cross-surface contracts stabilize.
- Whether optional OS-native scheduled checks are useful; this change deliberately keeps checks inside explicitly enabled interactive CLI use.
