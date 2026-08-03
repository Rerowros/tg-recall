## ADDED Requirements

### Requirement: Permanent agent setup URL
The project SHALL publish one documented agent-readable setup prompt at `https://raw.githubusercontent.com/Rerowros/tg-recall/main/docs/agent-setup/prompt.md`, and the public copy text SHALL not contain a release number.

#### Scenario: User copies setup prompt after a future release
- **WHEN** a user copies the documented setup instruction after the installed or latest release changes
- **THEN** the URL remains identical and the fetched document discovers the current release rather than embedding the old version in its URL

### Requirement: Release-backed package guidance
The setup prompt SHALL resolve a stable, non-draft, non-prerelease GitHub Release, select an exact versioned universal wheel asset, report its GitHub SHA-256 digest as integrity metadata, and SHALL NOT instruct installation from the mutable default branch.

#### Scenario: Agent prepares first installation
- **WHEN** `tg-recall` is not installed and public release metadata contains one valid universal wheel
- **THEN** the agent presents the exact release wheel URL, version, and digest in a copy-ready human command without executing a package installer

#### Scenario: Latest release lacks integration support
- **WHEN** the installed latest release has no `integrate list` command
- **THEN** the setup stops with a capability-gap explanation and does not fall back to installing source from `main`

### Requirement: Harness-aware setup plan
The setup prompt SHALL cover Codex, Claude Code, Cursor, and explicit generic destinations, use `integrate list` as the capability source of truth, require an explicit user or project scope, and preserve every reported manual action.

#### Scenario: Cursor user setup is requested
- **WHEN** the capability inventory reports automatic user MCP support but a manual Cursor User Rule
- **THEN** the setup plan reports both outcomes and does not claim complete automatic instruction installation

### Requirement: Human-controlled writes
The setup prompt SHALL limit AI execution to non-mutating diagnostics and SHALL stop with exact copy-ready commands before package installation or `integrate install`, `refresh`, or `uninstall`.

#### Scenario: Preview succeeds in an AI shell
- **WHEN** an agent completes a conflict-free preview
- **THEN** it explains the target files and asks the user to run the generated write command in a normal interactive terminal

### Requirement: Setup remains blind to Telegram data
The setup prompt SHALL forbid Telegram authentication, sync, archive/profile/config inspection, MCP tool calls, media or transcript processing, provider setup, and reading sessions, credentials, SQLite, wiki, or exports during installation.

#### Scenario: Agent is asked to verify setup
- **WHEN** the agent verifies installation status
- **THEN** it uses only integration status and harness discovery and does not start `tg-recall-mcp` or inspect a Telegram profile

### Requirement: Stable prompt contract
The setup document SHALL declare a positive integer contract version, remain deterministic and reviewable, include restart and verification instructions, and contain no secrets, private paths, shell-environment bypass, or destructive commands.

#### Scenario: Setup document is packaged and tested
- **WHEN** repository and distribution checks inspect the document
- **THEN** its stable URL, supported harnesses, human-write boundary, privacy exclusions, and forbidden bypasses are validated
