## ADDED Requirements

### Requirement: Versioned Codex routing prompt
The system SHALL expose a versioned human-readable and machine-readable Codex prompt containing workflow, budgets, model preferences, fallbacks, escalation criteria, and safety boundaries.

#### Scenario: Agent requests its guide
- **WHEN** `agent guide` is requested for Codex
- **THEN** the response includes prompt version, compatible `tg-recall` version, supported capabilities, initial/widened budgets, and exact allowed and forbidden operations

### Requirement: Separate-limit Spark preference
The Codex prompt SHALL prefer `gpt-5.3-codex-spark` for near-instant narrow read-only retrieval when the current Codex surface exposes it, its separate usage limit is available, and the task tolerates preview-model quality.

#### Scenario: Bounded archive lookup with Spark available
- **WHEN** the task is a clear read-only lookup that benefits from a subagent and Spark is selectable
- **THEN** the prompt recommends one `gpt-5.3-codex-spark` subagent with a bounded scope and compact cited output

### Requirement: Stable Luna fallback
The Codex prompt SHALL use `gpt-5.6-luna` at low or medium reasoning for narrow search, extraction, classification, and source expansion when Spark is unavailable, exhausted, or unsuitable.

#### Scenario: Spark is unavailable
- **WHEN** the current Codex runtime cannot select `gpt-5.3-codex-spark`
- **THEN** the workflow continues with Luna or the current model and does not fail the Telegram task

#### Scenario: Spark is selected
- **WHEN** Spark is available, its separate limit is usable, and the task is narrow, read-only, and tolerant of preview-model quality
- **THEN** it receives the same explicit scope, budget, citation, and no-write constraints as Luna

### Requirement: Evidence-based escalation
The routing prompt SHALL escalate from Luna/Spark only for ambiguous multi-source synthesis, repeated insufficient retrieval, security-critical review, migration/data-loss risk, or another stated quality boundary.

#### Scenario: Narrow model returns sufficient cited evidence
- **WHEN** the low-cost agent returns complete evidence under the acceptance criteria
- **THEN** the parent uses the result without repeating the same search on a stronger model

#### Scenario: Search result is materially ambiguous
- **WHEN** evidence conflicts or requires cross-source judgment beyond the narrow agent contract
- **THEN** the workflow returns the evidence summary to the parent or an explicitly stronger model for synthesis without rerunning completed retrieval

### Requirement: Bounded delegation
The routing prompt SHALL use no subagent for a single trivial direct lookup, one bounded search subagent by default, and multiple agents only for independent scopes whose parallelism materially improves the task.

#### Scenario: One direct lookup is sufficient
- **WHEN** one local query can produce the requested result directly
- **THEN** the prompt does not require a subagent turn

### Requirement: No Codex configuration mutation
The system SHALL NOT edit global or project Codex model configuration, custom agents, entitlements, or memory settings when generating or using the routing guide.

#### Scenario: Preferred model is not configured
- **WHEN** a preferred custom agent/model is absent
- **THEN** the guide reports a fallback and performs no configuration write
