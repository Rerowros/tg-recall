## Context

The current CLI owns safe, idempotent harness adapters, but onboarding begins
only after a person knows how to install the correct release and invoke the
right target/scope combination. Cloudflare's agent setup uses one stable
`Fetch .../prompt.md` instruction as a public bootstrap. `tg-recall` can use the
same discovery pattern, but its local archive and Telegram session require a
stricter separation between public diagnostics and configuration writes.

The earlier lifecycle boundary rejects every integration command in an AI
shell. That protects harness files, but also prevents an agent from safely
listing support, calculating a no-write preview, or checking whether its own
managed artifacts are present. None of those operations needs Telegram config,
credentials, a session, SQLite, media, wiki, exports, or network access.

## Goals / Non-Goals

**Goals:**

- Provide one permanent copy-paste URL that stays unchanged across releases.
- Let an AI determine supported harness behavior and perform bounded read-only
  setup diagnostics.
- Resolve package versions through the latest GitHub Release at setup time, use
  versioned wheel asset URLs, and expose integrity metadata without calling it
  a signature.
- Preserve explicit human authority for package installation and every harness
  file mutation.
- Keep setup independent of Telegram profiles and private archive contents.

**Non-Goals:**

- Run a remote MCP server, installer service, telemetry endpoint, or GitHub
  Pages deployment.
- Let an AI bypass lifecycle policy by clearing environment markers or adding a
  nominal approval flag.
- Authenticate Telegram, select chats, sync, inspect an archive, start MCP, or
  configure providers during installation.
- Guess undocumented paths for unsupported harnesses.

## Decisions

### Stable URL on the default branch

The public copy prompt is:

```text
Fetch https://raw.githubusercontent.com/Rerowros/tg-recall/main/docs/agent-setup/prompt.md and follow it.
```

The URL deliberately has no release number and therefore remains usable across
releases. The fetched document has its own integer contract version and must
remain backward-readable. A GitHub Pages site or custom domain would provide a
nicer URL but adds deployment state without improving the local privacy model.

The stable document is mutable repository content. It must never instruct an
agent to install from `main`; instead it resolves the latest non-draft,
non-prerelease GitHub Release, selects the exact versioned universal wheel, and
reports GitHub's SHA-256 digest for the human to verify. The digest provides
integrity metadata, not publisher authentication.

### Diagnostic versus write phases

AI/CI/automation shells may run only:

- `integrate list`, which is pure capability inventory;
- `integrate preview`, which reads only explicit harness targets and global
  integration ownership state and performs no writes;
- `integrate status`, which reads the same public integration surfaces.

They still may not run `integrate install`, `refresh`, or `uninstall`, any
`update` subcommand, or any package installer. The fetched prompt stops after
printing exact copy-ready human commands. A boolean flag is not accepted as
proof of human approval because an agent could set it itself.

### Setup prompt is data-blind

The prompt may inspect command availability, public release metadata, harness
configuration destinations during preview/status, and the stable JSON results
of integration diagnostics. It must not call `agent guide`, `doctor`, MCP,
profile/config commands, Telegram commands, or filesystem searches outside the
documented harness targets because those paths can open or reveal private
state.

### Harness-specific output with honest partial support

The prompt uses the CLI's capability inventory as the source of truth for
Codex, Claude Code, Cursor, and generic integrations. It does not claim that a
manual Cursor User Rule or Claude user MCP registration was written. Unknown
harnesses use generic mode only after the user supplies an explicit output root
and destinations.

## Risks / Trade-offs

- **[Mutable stable prompt]** → Keep setup logic reviewable, data-blind, and
  human-write-only; install packages only from versioned Release assets.
- **[Agent presents an unsafe command]** → Contract tests pin forbidden setup
  operations and require exact lifecycle/write warnings in the prompt.
- **[Latest release predates bootstrap support]** → Prompt checks
  `integrate list`; if absent, it reports that the latest release is not yet
  bootstrap-capable instead of installing from `main`.
- **[Read-only diagnostics leak local paths]** → JSON may identify documented
  harness targets but must not load Telegram config or serialize profile,
  credential, session, archive, media, wiki, or export values.
- **[Existing automation scripts expected all integration commands to fail]**
  → This is an intentional additive exception limited to non-mutating actions;
  write and update denial remains early and unconditional.

## Migration Plan

Land the stable prompt, gate change, tests, and documentation together. The
existing human commands remain compatible. Publish them in the next explicitly
authorized release; until that release contains `integrate`, the prompt reports
the capability gap. Rollback removes the prompt and restores the all-lifecycle
automation denial without changing any installed harness artifact.

## Open Questions

None. A branded documentation domain or plugin can later redirect to the same
stable contract without changing this bootstrap URL.
