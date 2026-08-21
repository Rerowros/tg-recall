## 1. Stdio Lifecycle

- [x] 1.1 Add unused/idle timeout and supervising-parent watchdog primitives that do not touch archive data.
- [x] 1.2 Run the stdio MCP server through the lifecycle loop instead of a blocking stdin iterator.
- [x] 1.3 Keep MCP tools, JSON-RPC payloads, and read-only policy unchanged.

## 2. Tests And Docs

- [x] 2.1 Cover timeout parsing, wrapper-parent walking, unused vs `tools/call`, idle, parent death, and EOF.
- [x] 2.2 Document defaults and `TG_RECALL_MCP_UNUSED_TIMEOUT_SEC`, `TG_RECALL_MCP_IDLE_TIMEOUT_SEC`, and `TG_RECALL_MCP_PARENT_WATCHDOG` in English and Russian public docs.
