## Why

The public project entry points are mostly English even though the primary
maintainer and part of the expected audience work in Russian. A complete,
navigable Russian documentation set makes installation, privacy boundaries,
agent setup, recovery, contribution, and roadmap decisions understandable
without changing the canonical CLI or machine-readable contracts.

## What Changes

- Add a complete `README.ru.md` and reciprocal language navigation in both
  README files, including the stable versionless agent-setup instruction.
- Add Russian counterparts for the public security, contribution, roadmap,
  changelog, recovery, maintenance, wiki, provider, export-pack, and agent-setup
  documents that are currently English-only.
- Keep commands, option names, JSON fields, citations, paths, URLs, model names,
  and privacy terminology exact across languages.
- Keep `docs/agent-setup/prompt.md` as the sole authoritative machine-executed
  setup contract; publish `prompt.ru.md` only as a clearly labelled human
  translation.
- Package the Russian public files in the sdist and add tests for locale
  navigation, required files, safe prompt separation, local links, and release
  privacy boundaries.

## Capabilities

### New Capabilities

- `russian-public-documentation`: Covers the supported Russian documentation
  set, language navigation, terminology and command fidelity, authoritative
  agent-prompt separation, and distribution/link validation.

### Modified Capabilities

<!-- No existing archived capability changes: CLI, JSON, MCP, storage, privacy,
     and release behavior remain unchanged. -->

## Impact

This change affects public Markdown documentation, README navigation, sdist
inclusion, and documentation/distribution tests. It adds no runtime dependency,
CLI command, network service, telemetry, data migration, harness write, or
access to Telegram profiles, sessions, databases, media, wiki, or exports.
