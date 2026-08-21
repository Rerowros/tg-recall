## ADDED Requirements

### Requirement: Abandoned stdio MCP processes self-reap
The stdio MCP server SHALL exit without host EOF when it has received no `tools/call` within the unused timeout, when it has received no request within the idle timeout after a `tools/call`, or when its supervising non-wrapper parent process is no longer the same live process.

#### Scenario: Unused Codex thread
- **WHEN** a host leaves `tg-recall-mcp` running after `initialize` or `tools/list` and sends no `tools/call` for 600 seconds
- **THEN** the server exits with a non-archive diagnostic on stderr and does not keep waiting for stdin EOF

#### Scenario: Idle after a tool call
- **WHEN** the server has handled a `tools/call` and then receives no further request for 1800 seconds
- **THEN** the server exits rather than remaining resident

#### Scenario: Supervising parent exits
- **WHEN** the first ancestor that is not a Python, uv, or `tg-recall-mcp` wrapper dies or is replaced
- **THEN** the server exits even if stdin is still open

#### Scenario: Operator disables a check
- **WHEN** `TG_RECALL_MCP_UNUSED_TIMEOUT_SEC` or `TG_RECALL_MCP_IDLE_TIMEOUT_SEC` is `0`, or `TG_RECALL_MCP_PARENT_WATCHDOG=0`
- **THEN** the corresponding reap check is disabled and stdin EOF still ends the server

### Requirement: Lifecycle does not change MCP access
Stdio lifecycle reaping SHALL NOT add MCP tools, write archive state, open a network listener, or send archive content in the exit diagnostic.

#### Scenario: Read-only surface is unchanged
- **WHEN** a connected client lists tools and calls an allowed read tool before any timeout
- **THEN** the tool set and JSON-RPC result remain the existing read-only archive interface
