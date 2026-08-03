## 1. Provider Boundary

- [x] 1.1 Define provider request/response, usage, error, and citation-reference protocols independent of vendor SDKs.
- [x] 1.2 Add backward-compatible provider/model/credential configuration and redaction without changing the extractive default.
- [x] 1.3 Implement a deterministic mock provider and contract tests before selecting a real adapter.

## 2. Policy And Context

- [x] 2.1 Enforce external-LLM enablement, allowed data classes, explicit archive scope, credentials, model, and evidence budgets before prompt construction.
- [x] 2.2 Serialize only bounded cited evidence and exclude credentials, sessions, paths, unrelated records, and database access.
- [x] 2.3 Add tests proving policy denial performs no provider/network call.

## 3. Answer Validation And Fallback

- [x] 3.1 Validate every provider evidence reference against the submitted context and resolve valid references to `tg://` citations.
- [x] 3.2 Reject unknown or wholly uncited synthesis and return provider status plus cited local evidence.
- [x] 3.3 Preserve existing extractive behavior and add stable JSON fields for synthesis/provider provenance additively.

## 4. Adapter And Audit

- [x] 4.1 Select one optional provider adapter after verifying current privacy controls, structured output, and Python 3.13 support.
- [x] 4.2 Record sanitized provider/model, policy, evidence IDs/count, token usage, latency, and failure class without full prompts by default.
- [x] 4.3 Verify the answer path cannot execute Telegram writes, auth, config mutation, purge, or autonomous tools.

## 5. Validation

- [x] 5.1 Document provider data boundaries, retention caveats, fallback semantics, and credential setup.
- [x] 5.2 Run mock/adapter, policy, citation, JSON-contract, full pytest, distribution privacy, and strict OpenSpec validation.
