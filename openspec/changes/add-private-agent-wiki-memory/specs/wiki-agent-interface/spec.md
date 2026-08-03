## ADDED Requirements

### Requirement: Compact wiki lookup
The system SHALL provide an agent interface that returns compact, relevant wiki content with page freshness and citations before raw archive retrieval.

#### Scenario: Answer from a current wiki page

- **WHEN** an agent asks for known communication preferences for a participant with a current page
- **THEN** the interface returns a compact cited page excerpt and snapshot metadata

### Requirement: Source expansion
The system SHALL provide an agent interface that expands cited wiki assertions to their original Telegram message context.

#### Scenario: Verify a wiki assertion

- **WHEN** an agent requests the source for a cited assertion
- **THEN** the interface returns the referenced raw message context with the original Telegram citation
