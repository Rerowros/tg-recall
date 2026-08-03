## 1. Pack Contract

- [x] 1.1 Define versioned manifest and file schemas for raw, wiki, and mixed packs.
- [x] 1.2 Implement strict scope/budget validation and normalized profile-neutral logical paths.
- [x] 1.3 Add manifest validation for hashes, sizes, citations, snapshot IDs, duplicates, traversal, absolute paths, and link escapes.

## 2. Generation

- [x] 2.1 Generate deterministic raw evidence JSONL with stable `tg://` citations from an explicit scope.
- [x] 2.2 Generate derived wiki Markdown and assertion/source mappings with freshness metadata.
- [x] 2.3 Write packs atomically below the profile exports directory by default and harden permissions best-effort.
- [x] 2.4 Support explicit external destinations without embedding absolute private paths in pack content.

## 3. Incremental Rebuild And Verification

- [x] 3.1 Reuse only hash-verified unchanged generated content from a previous immutable pack.
- [x] 3.2 Add offline pack verification that requires neither Telegram nor the source profile.
- [x] 3.3 Add tests for tampering, stale wiki data, interrupted generation, malicious paths, undeclared records, and privacy exclusions.

## 4. Interface And Validation

- [x] 4.1 Add additive CLI commands and stable JSON results for pack create, inspect, and verify.
- [x] 4.2 Update agent/export documentation to distinguish raw evidence, derived knowledge, exports, and backups.
- [x] 4.3 Run export, wiki integration, distribution privacy, backup/restore, full pytest, and strict OpenSpec validation.
