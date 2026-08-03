## Why

Raw JSONL exports are useful for one-off analysis but do not describe provenance, freshness, or the relationship between raw evidence and synthesized wiki knowledge. Private AI export packs provide a stable local handoff format without exposing the whole archive.

## What Changes

- Add bounded export packs sourced from selected raw evidence, wiki pages, or both.
- Add a manifest with profile-neutral scope metadata, schema version, timestamps, citations, source snapshot IDs, and content hashes.
- Keep default output below the selected profile's private exports directory and require an explicit path for external destinations.
- Support deterministic incremental regeneration and verification of pack integrity.
- Reject credentials, session material, absolute private paths, and records outside the declared scope.
- Add agent documentation for consuming a pack without treating derived text as uncited truth.

## Capabilities

### New Capabilities

- `private-ai-export-packs`: Covers scoped private export bundles, manifests, integrity, incremental regeneration, and privacy exclusions.

### Modified Capabilities

None. The canonical specs have not yet been archived.

## Impact

The change affects export services and CLI, wiki/retrieval read APIs, profile paths, backup/retention documentation, distribution privacy tests, and JSON schemas. It does not add network publication or automatic upload.
