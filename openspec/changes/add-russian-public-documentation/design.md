## Context

`README.md` and most operational documents are English, while two agent guides
are already Russian. There is no consistent locale navigation or rule for which
file is authoritative. The setup prompt adds a special security constraint:
agents fetch its stable English URL and must not accidentally execute a
translated or stale variant. The sdist currently includes top-level public
Markdown files explicitly and all files below `docs/`.

## Goals / Non-Goals

**Goals:**

- Give Russian readers a complete path from installation through security,
  harness setup, backup, retrieval, wiki, provider policy, and contribution.
- Make language selection visible at the top of both README files.
- Preserve commands, paths, flags, JSON names, URLs, and privacy guarantees.
- Validate the supported locale set and package it without widening the public
  artifact allowlist to private/generated data.

**Non-Goals:**

- Localize CLI output, JSON contracts, source code, package metadata, or MCP.
- Translate the MIT license or create a second legally authoritative license.
- Replace the canonical machine-readable agent setup prompt.
- Add an automated translation service or require every future document to be
  released in multiple languages atomically.

## Decisions

### Suffix-based locale files

Russian counterparts use `README.ru.md`, `SECURITY.ru.md`, and the equivalent
`name.ru.md` convention below `docs/`. This keeps English GitHub conventions
and package metadata stable while making locale identity explicit. A separate
localized branch or directory was rejected because it makes relative links and
release review harder.

### Supported important-document set

The required top-level set is README, SECURITY, CONTRIBUTING, ROADMAP, and
CHANGELOG. The required operational set covers backup/restore, archive
maintenance, AI export packs, wiki memory, the optional OpenAI provider, and
the stable setup contract. `harness-integration.md` and
`codex-agent-optimization.md` are already Russian and remain at their compatible
paths.

### One executable setup contract

`docs/agent-setup/prompt.md` remains the only URL advertised for `Fetch ... and
follow it`. `prompt.ru.md` is labelled as a human translation, links to the
canonical contract, and warns agents not to execute it. This avoids behavioral
drift and keeps existing setup tests and links stable.

### Tests protect structure, not prose identity

Tests require every supported file, reciprocal README navigation, safe prompt
separation, valid repository-local Markdown links, and sdist inclusion. They do
not compare natural-language paragraphs mechanically. Code fences and exact
identifiers are reviewed and preserved during translation.

## Risks / Trade-offs

- **[Translations drift after an English edit]** → Keep reciprocal links and a
  documented supported set; review both files when changing user-visible
  behavior.
- **[Translated setup prompt is executed by an agent]** → Never advertise its
  URL as a Fetch instruction and put a prominent human-reference warning at the
  top.
- **[Translation changes a command or privacy promise]** → Preserve code fences
  and technical identifiers verbatim and cover critical boundaries with tests.
- **[Sdist grows]** → Markdown growth is acceptable; distribution privacy tests
  continue rejecting private/generated paths.

## Migration Plan

Add locale files and links without renaming existing documents. Include new
top-level RU files explicitly in the sdist, run link/privacy tests and the full
suite, then publish in a separately authorized release. Rollback removes only
RU files, navigation links, and their tests; runtime behavior is unchanged.

## Open Questions

None.
