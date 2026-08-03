## 1. Versioned Guide Foundation

- [x] 1.1 Add one runtime version helper and replace the hard-coded `0.2.0` agent-guide version.
- [x] 1.2 Replace legacy version-specific guide keys and obsolete planning text while preserving additive JSON compatibility where valid.
- [x] 1.3 Add deterministic instruction renderers and golden tests proving all harnesses share guide version, budgets, routing, citations, and safety semantics.

## 2. Safe Integration Primitives

- [x] 2.1 Implement versioned integration result models, deterministic JSON output, harness/scope capability inventory, and explicit partial/manual/conflict states.
- [x] 2.2 Implement canonical target validation, symlink/reparse-point rejection, private adjacent backups, atomic replacement, and minimal global ownership state without profile data.
- [x] 2.3 Implement idempotent marked-block, dedicated-file, strict duplicate-key JSON merge, and owned TOML-block operations with fail-closed conflict handling.
- [x] 2.4 Add regression tests for malformed/duplicate/edited ownership markers, path escapes, no-op behavior, atomic failures, private backups, and preservation of unrelated content.

## 3. Harness Adapters

- [x] 3.1 Implement Codex project instructions and documented MCP configuration with honest user/project capability reporting.
- [x] 3.2 Implement Claude Code project/user instructions and documented MCP configuration or official-CLI/manual fallback.
- [x] 3.3 Implement Cursor project rules and project/user MCP JSON while returning a manual user-rule action.
- [x] 3.4 Implement generic integration with explicit bounded destinations and no guessed home paths.
- [x] 3.5 Add install/refresh/status/uninstall idempotency tests for each harness and mixed `all` results.

## 4. Release Update Management

- [x] 4.1 Add global/portable update cache paths and strict release/cache models with semantic-version validation.
- [x] 4.2 Implement bounded GitHub Release transport, ETag/fresh/stale/offline cache behavior, exact stable wheel validation, and secret-free results.
- [x] 4.3 Implement installation provenance detection from distribution metadata without reading profile/config/archive state.
- [x] 4.4 Implement verified wheel staging and supported `uv tool` apply with fixed argument vectors, digest/version validation, post-install verification, and manual fallbacks.
- [x] 4.5 Add tests for malformed/injected/oversized/redirected release data, cache states, provenance variants, digest mismatch, installer failure, Windows lock failure, and false-success prevention.

## 5. CLI and Execution Boundary

- [x] 5.1 Add `update check|status|configure|apply` parsing, handlers, human output, and stable JSON contracts.
- [x] 5.2 Add `integrate list|preview|install|status|refresh|uninstall` parsing, handlers, target/scope validation, and stable JSON contracts.
- [x] 5.3 Add an early lifecycle gate that rejects every update/integration command in AI/CI/automation mode before config, credentials, harness files, network, subprocess, or SQLite access, without trusted-automation bypass.
- [x] 5.4 Add explicit opt-in periodic update configuration and cache-aware eligible interactive notices while excluding help, MCP, AI/CI, and unhandled commands.
- [x] 5.5 Add CLI regression tests for dry-run, mixed status, error exits, deterministic JSON, early denial, and absence of sensitive paths or values.

## 6. MCP, Documentation, and Distribution

- [x] 6.1 Add concise MCP initialization instructions from the canonical guide and prove `tools/list` remains the exact read-only allowlist.
- [x] 6.2 Document release-wheel installation, editable-development limitations, update lifecycle, harness-specific user/project behavior, safe backups, and copy-ready manual fallbacks.
- [x] 6.3 Update stale optimization documentation and ensure prompts never claim available v0.5 capabilities are still planned for v0.2.
- [x] 6.4 Extend distribution/privacy tests so caches, downloaded wheels, integration state/backups, generated harness files, and private Telegram paths cannot enter wheel, sdist, or release assets.

## 7. Verification

- [x] 7.1 Run focused guide, integration, update, CLI boundary, MCP, and distribution tests.
- [x] 7.2 Run Ruff, full pytest, build/package inspection, `tg-recall --help`, JSON smoke checks, and strict OpenSpec validation.
- [x] 7.3 Review the complete diff for JSON compatibility, privacy, path safety, subprocess injection, and unrelated user-change preservation.
