## ADDED Requirements

### Requirement: Stable release discovery
The system SHALL expose `tg-recall update check` with human and JSON output that compares the installed semantic version with the latest non-draft, non-prerelease GitHub Release, uses a bounded network timeout, and reports `current`, `update_available`, `unreachable`, or `unsupported` without reading profile data.

#### Scenario: New stable release exists
- **WHEN** the installed version is older than a valid stable GitHub Release
- **THEN** the command returns the installed version, release version, release URL, compatible wheel metadata, digest when published by GitHub, and status `update_available`

#### Scenario: Release endpoint is unavailable
- **WHEN** the bounded release request fails and no valid fresh cache can satisfy it
- **THEN** the command returns status `unreachable`, a sanitized error code, and a nonzero exit status without changing the installation

### Requirement: Privacy-aware periodic checks
The system SHALL keep periodic update checks disabled until a human explicitly enables them, store only release metadata and timestamps in the global application cache, respect portable mode, and allow checks to be disabled without opening any archive, profile database, session, credential, media, wiki, or export path.

#### Scenario: Periodic checks are disabled
- **WHEN** a normal CLI command runs with update checks disabled
- **THEN** the system performs no update network request and emits no update notice

#### Scenario: Enabled cached check is still fresh
- **WHEN** a human enabled periodic checks and the cached successful check is younger than the configured interval
- **THEN** the system uses the cache and performs no network request

### Requirement: Explicit verified update application
The system SHALL expose a human-only `tg-recall update apply` operation that detects installation provenance, selects only a wheel asset matching the expected package and release version, verifies its SHA-256 digest before invoking a supported package installer, and never reports success unless the installer exits successfully.

#### Scenario: Supported uv tool installation is updated
- **WHEN** a human explicitly applies an available release from a supported non-editable `uv tool` installation and the wheel digest is valid
- **THEN** the system invokes `uv` with an argument vector and the verified local wheel, reports the attempted and resulting versions, and recommends integration refresh

#### Scenario: Editable checkout is detected
- **WHEN** `update apply` runs from an editable or otherwise unsupported installation
- **THEN** the system refuses to mutate it and returns an exact manual update instruction and installation provenance

#### Scenario: Digest does not match
- **WHEN** the downloaded wheel SHA-256 differs from the release digest
- **THEN** the system deletes the staged artifact, performs no installer invocation, and returns a verification failure

### Requirement: Update commands are excluded from agent execution
All update commands, including read-only checks and status, SHALL be classified as human-only, SHALL be rejected in detected AI or automation shells before loading tg-recall configuration, and SHALL never be exposed as MCP tools. Trusted-automation confirmation SHALL NOT bypass this harness-lifecycle boundary.

#### Scenario: Agent tries to inspect or apply updates
- **WHEN** an AI or automation environment invokes any update command
- **THEN** the command is denied before configuration or credential loading, network access, file write, download, or subprocess execution

### Requirement: Backward-compatible update JSON contract
Update command JSON SHALL include a schema version, action, status, installed version, optional release and provenance objects, changed paths, warnings, and next actions; future additions SHALL be additive within the same schema version.

#### Scenario: Machine-readable check
- **WHEN** `tg-recall --json update check` completes in any supported status
- **THEN** stdout contains exactly one JSON document with stable keys and no credentials, archive paths, or exception traceback
