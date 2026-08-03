## ADDED Requirements

### Requirement: Extractive default and fallback
The system SHALL keep local extractive answers as the default and SHALL return cited local evidence when an optional LLM provider is disabled, unavailable, or produces an invalid response.

#### Scenario: Default configuration
- **WHEN** the user runs `ask` without an external provider configured
- **THEN** the system performs no provider call and returns the existing local cited evidence behavior

#### Scenario: Provider outage
- **WHEN** an allowed provider call fails transiently
- **THEN** the result reports provider unavailability and includes bounded cited local evidence

### Requirement: Explicit provider policy gate
The system SHALL call an external LLM only when provider usage, allowed data classes, selected scope, credentials, and model are all explicitly configured.

#### Scenario: External LLM policy disabled
- **WHEN** a non-extractive provider is configured but external LLM policy is disabled
- **THEN** the system denies the provider call before prompt construction or network access

#### Scenario: Scope is unresolved
- **WHEN** an agent asks a question without an explicit allowed archive scope
- **THEN** the system does not retrieve archive-wide evidence and does not call the provider

### Requirement: Bounded provider context
The system SHALL send only the authorized evidence selected under explicit item and token budgets and SHALL NOT send credentials, sessions, archive paths, or unrelated records.

#### Scenario: Evidence exceeds token budget
- **WHEN** retrieved candidates exceed the provider context budget
- **THEN** the core truncates or reranks evidence before provider serialization and records the applied budget

### Requirement: Citation-validated synthesis
The system SHALL require synthesized answers to reference only evidence identifiers supplied in the provider context.

#### Scenario: Provider returns an unknown citation
- **WHEN** a provider response references evidence outside the submitted context
- **THEN** validation rejects the synthesized result and returns cited local evidence with an invalid-response status

#### Scenario: Valid cited answer
- **WHEN** every answer citation resolves to submitted evidence
- **THEN** the structured result includes synthesized text, resolved `tg://` citations, and provider provenance

### Requirement: Privacy-preserving provider audit
The system SHALL audit provider/model, policy decision, evidence identifiers/count, usage, latency, and sanitized failure class without storing credentials or full prompts by default.

#### Scenario: Provider request completes
- **WHEN** a provider returns a valid answer
- **THEN** a local audit event records the bounded metadata and no raw credential or session data

### Requirement: Read-only assistant behavior
The system SHALL NOT expose Telegram write, authorization, configuration mutation, purge, or autonomous tool execution through the LLM answer path.

#### Scenario: Provider requests an action
- **WHEN** provider output asks to send a Telegram message or invoke a forbidden command
- **THEN** the system treats it as answer text only or rejects it and performs no action
