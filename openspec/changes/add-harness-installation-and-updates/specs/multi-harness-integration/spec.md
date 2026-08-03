## ADDED Requirements

### Requirement: Supported harness inventory
The system SHALL expose integration adapters for `codex`, `claude-code`, `cursor`, and `generic`, and each adapter SHALL declare the components and scopes it can install rather than silently approximating an unsupported platform feature.

#### Scenario: Harness capabilities are listed
- **WHEN** a human runs `tg-recall integrate list` or its JSON form
- **THEN** the result identifies supported MCP, instruction, user-scope, project-scope, status, refresh, and uninstall behavior for every harness

### Requirement: Canonical versioned instructions
All installed instruction artifacts and MCP server instructions SHALL be rendered from the same structured `AgentGuide` source and SHALL include its schema version, prompt version, installed `tg-recall` version, bounded retrieval workflow, citation requirement, Spark/Luna availability-safe routing, and safety boundaries.

#### Scenario: Different harness artifacts are rendered
- **WHEN** Codex, Claude Code, Cursor, and generic instructions are generated for the same installed release
- **THEN** platform syntax may differ but the guide version, operational policy, budgets, and forbidden operations are semantically identical

### Requirement: Correct installed version reporting
The agent guide SHALL derive its compatibility version from package metadata and SHALL not expose legacy version-specific field names or claim that implemented capabilities are merely planned.

#### Scenario: Version 0.5.0 guide is generated
- **WHEN** the installed package version is `0.5.0`
- **THEN** human, JSON, MCP, and harness artifacts report `0.5.0` and current capability labels without `current_v0_2` or obsolete v0.2 planning text

### Requirement: Idempotent scoped installation
The system SHALL expose `integrate preview`, `install`, `status`, `refresh`, and `uninstall` for one harness or `all`, with explicit `user` or `project` scope and an explicit project root where required. Repeating install or refresh with identical inputs SHALL not create duplicate configuration or instruction blocks.

#### Scenario: Repeated project installation
- **WHEN** a project integration is installed twice with the same harness and guide version
- **THEN** the second operation reports `unchanged` and every owned MCP entry or instruction appears exactly once

#### Scenario: Unsupported scope component
- **WHEN** a harness has no safe file or official CLI mechanism for one requested component in that scope
- **THEN** the result reports `partial` with a copy-ready manual action and does not write to undocumented client state

### Requirement: Non-destructive configuration ownership
The installer SHALL modify only the `tg-recall` MCP entry and explicitly marked `tg-recall` instruction artifact or block, preserve unrelated content, refuse an unowned conflicting entry, create a private timestamped backup before modifying an existing file, and use atomic replacement without following a symlink outside the selected target root.

#### Scenario: Existing unrelated configuration is present
- **WHEN** installation targets a valid client configuration containing other MCP servers and user instructions
- **THEN** those entries remain semantically unchanged and the result lists only files changed for `tg-recall`

#### Scenario: Unowned tg-recall entry conflicts
- **WHEN** a target already contains a `tg-recall` entry not marked or structurally identical to the managed entry
- **THEN** installation stops with a conflict result and does not overwrite the file

#### Scenario: Target escapes through symlink
- **WHEN** a selected project target or child path resolves outside the selected project root through a symlink or junction
- **THEN** the operation is denied before backup or mutation

### Requirement: Harness-specific supported configuration
The Codex adapter SHALL install documented stdio MCP configuration plus project `AGENTS.md` or user `~/.codex/AGENTS.md` guidance. The Claude Code adapter SHALL use documented MCP scope configuration plus project or documented user `CLAUDE.md` guidance. The Cursor adapter SHALL use documented `mcp.json` configuration plus project rules and SHALL report a manual user-rule step instead of changing internal settings. The generic adapter SHALL require explicit MCP and instruction destinations below an explicit output root.

#### Scenario: Project harnesses are installed
- **WHEN** installation is requested for all harnesses in a project root
- **THEN** each adapter writes only documented project files or invokes a documented client CLI, and reports any unavailable client executable as a non-destructive partial result

### Requirement: Read-only MCP remains read-only
Harness installation SHALL register only `tg-recall-mcp`, and MCP initialization MAY advertise concise server instructions but SHALL expose no update, integration, authentication, purge, configuration, credential, or message-sending tool.

#### Scenario: Tools are listed after integration
- **WHEN** any installed harness initializes `tg-recall-mcp` and requests `tools/list`
- **THEN** the existing scoped read-only tool set is returned and no mutating lifecycle command is present

### Requirement: Integration lifecycle is human-only
All integration commands, including list, preview, and status, SHALL be denied in detected AI or automation shells before loading tg-recall configuration or reading harness configuration. Trusted-automation confirmation SHALL NOT bypass this boundary.

#### Scenario: Agent attempts integration install
- **WHEN** `TG_RECALL_AI_MODE=1` and `integrate install` is invoked
- **THEN** the command is rejected before tg-recall configuration or credential loading, client detection, harness configuration reads, backup, file mutation, or subprocess execution

### Requirement: Stable integration JSON and exit behavior
Every integration command SHALL emit one versioned JSON result containing action, requested harnesses, scope, per-harness status, changed paths, backup paths, conflicts, warnings, manual actions, and overall status, with deterministic ordering and no secret values.

#### Scenario: Mixed all-harness result
- **WHEN** `--json integrate install --harness all` has successful, unchanged, and partial adapters
- **THEN** the result preserves deterministic per-harness details, returns an overall partial status, and does not treat unsupported prompt installation as full success
