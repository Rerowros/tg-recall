## Context

`tg-recall-mcp` reads JSON-RPC lines from stdin until EOF. Codex/ChatGPT on
Windows starts a fresh server per thread, never closes the pipe while
`codex.exe` lives, and the uv console entrypoint triples each instance
(`tg-recall-mcp.exe` → python shim → CPython). Abandoned copies stay in
`for line in sys.stdin`.

## Goals / Non-Goals

**Goals:**

- Reap unused and idle stdio servers without host cooperation.
- Exit when the supervising non-wrapper parent dies, including Windows
  cases where EOF never arrives.
- Keep defaults on, with env overrides for operators.

**Non-Goals:**

- Switch MCP to HTTP/SSE or a singleton daemon.
- Change tool schemas, policy checks, or archive I/O.
- Fix host-side leaks such as Codex `node_repl.exe`.

## Decisions

### Wall-clock unused vs idle

`tools/list` and `initialize` do not count as use. Unused timeout is measured
from process start until the first `tools/call`, so keepalives cannot pin an
abandoned instance. After a tool call, idle timeout is measured from the last
received request line.

Defaults are 600s unused and 1800s idle. `0` disables that check.

### Watch the first non-wrapper ancestor

Walk the parent chain, skipping `python`, `uv`, and `tg-recall-mcp` wrappers,
and remember that PID plus creation time. On Windows this is `codex.exe` rather
than the uv trampoline that dies only when we die.

### Stay on stdio

A localhost HTTP server would multiplex clients, but it adds a listener and
changes the privacy/transport contract. Self-reaping stdio is the smaller
fix; hosts may restart the process on the next tool call.

## Risks / Trade-offs

- A live thread that does not call a tool for 10 minutes is reaped; the host
  must respawn on the next call or the call fails.
- PID reuse is mitigated with creation time where the OS exposes it; if that
  is unavailable, a reused PID could delay exit.
- Very short test timeouts exercise the same loop as production; the reader
  thread is daemonic so process shutdown does not wait on a stuck stdin.
