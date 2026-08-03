## ADDED Requirements

### Requirement: Progressive bounded retrieval
The system SHALL retrieve knowledge in ordered stages from reusable compact state to narrow raw evidence and SHALL expand larger context or media only when prior stages are insufficient or explicitly requested.

#### Scenario: Compact knowledge is sufficient
- **WHEN** a current cited wiki or evidence-set result answers the task within the initial budget
- **THEN** the system returns it without exporting a chat or expanding unrelated raw messages

#### Scenario: Initial evidence is insufficient
- **WHEN** the first bounded retrieval cannot meet the required evidence criteria
- **THEN** the system reports insufficiency and may perform one configured bounded widening without removing scope limits

### Requirement: Explicit context and work budgets
The system SHALL accept and enforce limits for returned items, context radius, estimated tokens, retrieval stages, retries, and tool calls supported by the host workflow.

#### Scenario: Result exceeds token budget
- **WHEN** candidate evidence exceeds the requested token budget
- **THEN** the system deduplicates and truncates lower-ranked context while preserving complete citations for returned items

#### Scenario: Budget is exhausted
- **WHEN** the configured retrieval-stage or retry budget is exhausted before sufficient evidence is found
- **THEN** the system stops and returns a structured incomplete result rather than silently widening to the full archive

### Requirement: Retrieval token budget semantics
The system SHALL define `token_budget` as a cap on the estimated final serialized retrieval payload and SHALL NOT present it as the total Codex context window, billed token usage, or credit consumption.

#### Scenario: Payload contains Russian text and JSON metadata
- **WHEN** evidence is serialized for an agent
- **THEN** token accounting includes the exact returned text, citations, metadata, escaping, and JSON structure rather than multiplying message characters by a fixed universal ratio

#### Scenario: Exact tokenizer is unavailable
- **WHEN** no verified local tokenizer mapping exists for the selected model
- **THEN** the system uses a versioned conservative estimator calibrated on representative multilingual Telegram payloads and reports that estimator in the result

#### Scenario: Candidate exceeds remaining usable budget
- **WHEN** a serialized evidence item does not fit after the configured safety margin
- **THEN** the system excludes it or explicitly truncates its evidence body while retaining complete citation and truncation metadata

### Requirement: Token accounting report
The system SHALL return requested budget, usable payload budget, estimated tokens, counter name/version, safety margin, and truncation state with every bounded retrieval result.

#### Scenario: Host later provides actual usage
- **WHEN** Codex or another host supplies actual input, cached-input, reasoning, or output token counts
- **THEN** the system records them separately from retrieval-payload estimates with model and source provenance

### Requirement: Reusable evidence sets
The system SHALL persist immutable profile-local evidence sets with scope, query purpose, source references/versions, retrieval provenance, budgets, compact summary, and citations.

#### Scenario: Reuse a current evidence set
- **WHEN** a later turn requests the same research purpose and all referenced sources remain current
- **THEN** the system returns the saved compact evidence set without repeating raw retrieval

#### Scenario: Source changed after evidence capture
- **WHEN** a referenced message, transcript, or wiki revision changes
- **THEN** the evidence set remains inspectable but is marked stale and refreshed only through an explicit bounded operation

### Requirement: Deduplicated context windows
The system SHALL merge overlapping message and transcript windows before returning context to an agent.

#### Scenario: Multiple hits share surrounding messages
- **WHEN** search hits expand to overlapping context ranges in the same chat
- **THEN** each source message appears once with all applicable hit/citation metadata

### Requirement: Usage telemetry with provenance
The system SHALL record retrieval stages, tool calls, returned/deduplicated items, retries, elapsed time, estimated tokens, and evidence reuse, and SHALL label provider-reported actual usage separately.

#### Scenario: Only an estimate is available
- **WHEN** Codex or a provider does not return billed token usage
- **THEN** the session report labels the value as an estimate and does not calculate authoritative credits or cost

#### Scenario: Host reports actual usage
- **WHEN** the calling host supplies actual input, cached-input, output, or reasoning token counts
- **THEN** the system stores those values separately with source/model metadata

### Requirement: Quality-preserving optimization gate
The system SHALL evaluate context optimization on representative sanitized tasks and SHALL reject a default change that saves resources but loses required answer evidence or citation coverage.

#### Scenario: Smaller budget degrades citations
- **WHEN** an optimized configuration reduces tokens but omits a required source citation compared with baseline
- **THEN** the configuration fails the optimization gate
