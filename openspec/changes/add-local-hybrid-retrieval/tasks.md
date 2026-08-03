## 1. Provider And Schema

- [x] 1.1 Define the embedding provider protocol, explicit local configuration, and optional dependency boundary.
- [x] 1.2 Select and integrate one Python 3.13-compatible local runtime without automatic model download.
- [x] 1.3 Add migrations for vector records, model/dimension metadata, source hashes, freshness, and checkpoint state.

## 2. Index Lifecycle

- [x] 2.1 Implement local embedding for selected message/transcript batches with bounded checkpoints.
- [x] 2.2 Detect missing, stale, and incompatible vectors by source hash and model identity.
- [x] 2.3 Add resumable build, status, rebuild, and explicit removal commands with stable JSON output.

## 3. Hybrid Retrieval

- [x] 3.1 Build independently bounded FTS and vector candidate sets after shared scope/filter enforcement.
- [x] 3.2 Implement documented deterministic score normalization/fusion and ranking provenance.
- [x] 3.3 Deduplicate message/transcript hits and assemble cited context windows under result/token budgets.
- [x] 3.4 Implement automatic local fallback and strict semantic-unavailable behavior without labeling token overlap as vectors.

## 4. Interfaces And Validation

- [x] 4.1 Expose retrieval mode, model/index freshness, fallback reason, and batch evidence through CLI and read-only MCP.
- [x] 4.2 Extend `doctor` with optional runtime and vector-index compatibility checks.
- [x] 4.3 Add deterministic fixture tests for ranking, filters, deduplication, stale models, interruption/resume, and no-provider fallback.
- [x] 4.4 Run full pytest, backup/restore and distribution privacy checks, performance smoke tests, and strict OpenSpec validation.
