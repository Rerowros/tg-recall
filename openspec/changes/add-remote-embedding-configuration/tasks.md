## 1. Configuration and Human Setup

- [x] 1.1 Define a validated semantic provider/model catalog and additive profile fields for the explicit OpenRouter policy and request timeout.
- [x] 1.2 Add human-only `config embeddings choices` and `config embeddings setup` commands with disabled, local, and catalog-backed OpenRouter selections; require explicit remote-text acknowledgement.
- [x] 1.3 Preserve redacted/profile-safe configuration output and reject setup in automation before any configuration or network action.

## 2. OpenRouter Provider

- [x] 2.1 Implement an stdlib HTTPS OpenRouter embedding provider with environment-only `OPENROUTER_API_KEY`, bounded payloads, timeouts, response validation, and secret-safe errors.
- [x] 2.2 Discover vector dimensions using a non-archive probe and construct stable provider/model/dimension metadata without mixing incompatible vectors.
- [x] 2.3 Wire the provider factory into indexing and retrieval, enforce explicit provider policy, and retain automatic FTS fallback versus strict semantic errors.

## 3. Tests and Documentation

- [x] 3.1 Add unit and CLI coverage for catalog selection, consent, automation denial, missing key/policy, safe HTTP request handling, provider metadata, and fallback behavior.
- [x] 3.2 Update English and Russian documentation with setup examples, selected-scope egress, API-key handling, model choices/pricing units, indexing lifecycle, and fallback semantics.
- [x] 3.3 Run focused embedding/config tests, Ruff, full pytest, `git diff --check`, and strict OpenSpec validation.
