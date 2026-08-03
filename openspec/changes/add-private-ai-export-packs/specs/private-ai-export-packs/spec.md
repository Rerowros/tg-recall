## ADDED Requirements

### Requirement: Explicit bounded export scope
The system SHALL require an explicit chat/date or saved scope and a bounded record/token budget before creating an AI export pack.

#### Scenario: Export requested without scope
- **WHEN** a user or agent requests an AI pack without an explicit permitted scope
- **THEN** the system refuses without reading or exporting the full archive

### Requirement: Manifested provenance and integrity
The system SHALL include a manifest with schema version, declared scope, generator version, timestamps, source snapshot IDs, citations, logical file paths, sizes, and content hashes.

#### Scenario: Verify an unchanged pack
- **WHEN** verification recomputes every declared file hash and validates the manifest
- **THEN** it reports the pack as intact without requiring Telegram or the source archive

#### Scenario: Pack file is modified
- **WHEN** a content file no longer matches its manifest hash
- **THEN** verification fails with the affected logical path and does not consume the modified pack as trusted

### Requirement: Evidence and synthesis separation
The system SHALL label raw evidence separately from synthesized wiki content and SHALL retain source citation mappings for derived assertions.

#### Scenario: Mixed raw and wiki pack
- **WHEN** a pack contains a wiki decision page and its supporting messages
- **THEN** the manifest identifies the derived page, freshness/snapshot metadata, and citations to raw evidence entries

### Requirement: Private portable output
The system SHALL write packs below the selected profile exports directory by default and SHALL exclude credentials, sessions, absolute private paths, and undeclared records.

#### Scenario: Default export destination
- **WHEN** no output path is supplied
- **THEN** the completed pack is atomically written below the active profile exports directory with best-effort private permissions

#### Scenario: External destination
- **WHEN** an external path is explicitly supplied
- **THEN** the system uses only that resolved destination and records that choice without embedding the absolute path in portable content

### Requirement: Incremental immutable regeneration
The system SHALL reuse unchanged generated content by verified hash and SHALL create a new pack rather than mutating a previous completed pack.

#### Scenario: Only one wiki page changed
- **WHEN** regeneration uses a previous verified manifest and one source page has changed
- **THEN** unchanged content remains byte-identical and the new pack records only the new manifest generation

### Requirement: Safe pack verification
The system SHALL reject absolute paths, path traversal, symbolic-link escapes, duplicate logical paths, and undeclared files where strict verification is requested.

#### Scenario: Malicious manifest path
- **WHEN** a manifest contains `../` or an absolute file path
- **THEN** verification rejects the pack before reading or writing outside the pack root
