## ADDED Requirements

### Requirement: AI may run non-mutating integration discovery
Detected AI, CI, and automation shells SHALL be allowed to run `integrate list`, `integrate preview`, and `integrate status` while preserving their existing deterministic JSON and no-write behavior.

#### Scenario: Agent lists harness capabilities
- **WHEN** `TG_RECALL_AI_MODE=1` runs `tg-recall integrate list --json`
- **THEN** the command returns the public harness capability matrix without loading Telegram configuration, credentials, session, SQLite, media, wiki, or exports

#### Scenario: Agent previews an explicit project target
- **WHEN** an AI shell runs `integrate preview` for an explicit project root
- **THEN** the command may read only the documented harness targets and integration ownership state, reports the planned changes, and performs no backup or mutation

#### Scenario: Agent inspects installed integration status
- **WHEN** an AI shell runs `integrate status` for an explicit target and scope
- **THEN** the command reads only harness integration artifacts and ownership state and returns no Telegram profile or secret values

### Requirement: Mutating lifecycle remains human-only
Detected AI, CI, and automation shells SHALL reject `integrate install`, `integrate refresh`, `integrate uninstall`, and every `update` action before package configuration, Telegram state, harness mutation, network, or subprocess access, and trusted-automation flags SHALL NOT bypass the denial.

#### Scenario: Agent attempts setup write
- **WHEN** an AI shell runs `integrate install` after a successful preview
- **THEN** the command returns the stable automation denial before reading or writing a harness file

#### Scenario: Agent attempts update status
- **WHEN** an AI shell invokes any `update` action including `status`
- **THEN** the command remains denied before cache, network, provenance, or installer access

### Requirement: Read-only discovery cannot broaden agent authority
Allowing integration discovery SHALL NOT add an MCP tool, permit Telegram write/auth/config actions, start the MCP server, clear automation markers, or create an approval-token bypass.

#### Scenario: MCP tools are listed after the gate change
- **WHEN** `tg-recall-mcp` returns `tools/list`
- **THEN** the exact existing read-only tool allowlist is unchanged and contains no integration or update tool
