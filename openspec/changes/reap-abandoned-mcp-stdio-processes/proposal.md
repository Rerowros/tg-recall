## Why

Windows hosts such as Codex spawn a new stdio `tg-recall-mcp` for every
thread and keep stdin open after the thread is abandoned. The server only
exited on EOF, so `python.exe` copies accumulated for as long as the host
process stayed alive.

## What Changes

- Exit the stdio MCP loop on unused timeout, idle timeout, or supervising
  parent death, in addition to stdin EOF.
- Keep the read-only tool surface, JSON-RPC payloads, and stdio transport
  unchanged.
- Allow operators to disable each check through environment variables.

## Capabilities

### New Capabilities

- `mcp-stdio-lifecycle`: Self-reap abandoned stdio MCP processes without
  changing archive access or adding a network listener.

### Modified Capabilities

- None.

## Impact

Changes the stdio serve loop, adds focused lifecycle tests, and documents
the default timeouts in English and Russian public docs. No archive data,
credentials, MCP tools, or listening port are added.
