## Context

The current export command writes scoped JSONL with optional transcript and media metadata. Wiki memory will introduce derived Markdown and snapshot provenance. Downstream AI tools need a compact, verifiable handoff that remains private and does not confuse synthesized assertions with raw evidence.

## Goals / Non-Goals

**Goals:**

- Define a deterministic export directory/ZIP contract with a manifest.
- Support raw, wiki, and mixed packs under explicit scope and budget.
- Make packs portable across operating systems without absolute paths.
- Verify integrity and incrementally reuse unchanged artifacts.

**Non-Goals:**

- Upload or publish packs automatically.
- Export Telegram sessions, credentials, raw media binaries by default, or the entire archive implicitly.
- Make export packs a replacement for backup/restore.

## Decisions

### Manifest-first pack format

Each pack contains `manifest.json` plus content files addressed by stable logical paths. The manifest records format/schema version, created time, source profile alias (not filesystem path), declared scope, budgets, snapshot IDs, citations, content hashes, and generator version. Deterministic ordering enables verification and incremental rebuilds.

### Separate evidence and derived layers

Raw evidence is JSONL with stable citations. Derived wiki content is Markdown plus assertion/source mappings. The manifest labels each file's layer and freshness so a consumer cannot treat synthesized text as primary evidence.

### Private destination policy

The default destination is the selected profile exports directory. Any external output path must be supplied explicitly. Temporary files remain in the profile cache, finalization is atomic, and permissions are hardened best-effort.

### Incremental generation by content hash

A new pack may reference a prior local manifest and copy/reuse only identical generated files. It never follows external paths from an untrusted manifest and never mutates previous packs.

## Risks / Trade-offs

- Packs can still contain highly sensitive conversations → require explicit scope, show estimated contents, harden permissions, and document secure deletion limitations.
- Derived pages may be stale → include freshness and snapshot IDs and allow policy to reject stale wiki content.
- ZIP extraction can be unsafe → use normalized relative paths and verification that rejects traversal or links.

## Migration Plan

This is additive to current JSONL export. Keep existing command behavior and introduce a new pack subcommand/format. Old exports remain readable but are not retroactively assigned manifests.

## Open Questions

- Whether the first release should support directory packs only or both directory and ZIP after the same verifier is complete.
