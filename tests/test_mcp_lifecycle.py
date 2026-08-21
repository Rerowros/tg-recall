from __future__ import annotations

import io
import json
import os
import time
from typing import Any

from tg_recall.mcp_lifecycle import (
    DEFAULT_IDLE_TIMEOUT_SEC,
    DEFAULT_UNUSED_TIMEOUT_SEC,
    IDLE_TIMEOUT_ENV,
    PARENT_WATCHDOG_ENV,
    UNUSED_TIMEOUT_ENV,
    ParentSnapshot,
    ProcessInfo,
    SessionState,
    StdioLifecyclePolicy,
    next_exit_reason,
    parse_bool_env,
    parse_timeout_seconds,
    request_method,
    resolve_supervising_parent,
    serve_stdio,
    snapshot_supervising_parent,
)


def _handle(request: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request.get("id"), "result": {"method": request.get("method")}}


def _open_pipe() -> tuple[io.TextIOWrapper, io.TextIOWrapper]:
    read_fd, write_fd = os.pipe()
    stdin = os.fdopen(read_fd, "r", encoding="utf-8")
    writer = os.fdopen(write_fd, "w", encoding="utf-8", buffering=1)
    return stdin, writer


def test_parse_timeout_seconds_defaults_zero_and_invalid() -> None:
    assert parse_timeout_seconds(None, 600) == 600
    assert parse_timeout_seconds(" ", 600) == 600
    assert parse_timeout_seconds("0", 600) is None
    assert parse_timeout_seconds("-1", 600) is None
    assert parse_timeout_seconds("12.5", 600) == 12.5
    assert parse_timeout_seconds("nope", 600) == 600


def test_parse_bool_env_defaults() -> None:
    assert parse_bool_env(None, True) is True
    assert parse_bool_env("0", True) is False
    assert parse_bool_env("off", True) is False
    assert parse_bool_env("yes", False) is True
    assert parse_bool_env("maybe", True) is True


def test_policy_from_environ_reads_timeouts_and_watchdog() -> None:
    policy = StdioLifecyclePolicy.from_environ(
        {
            UNUSED_TIMEOUT_ENV: "0",
            IDLE_TIMEOUT_ENV: "45",
            PARENT_WATCHDOG_ENV: "off",
        }
    )

    assert policy.unused_timeout_sec is None
    assert policy.idle_timeout_sec == 45
    assert policy.parent_watchdog is False
    assert StdioLifecyclePolicy.from_environ({}).unused_timeout_sec == DEFAULT_UNUSED_TIMEOUT_SEC
    assert StdioLifecyclePolicy.from_environ({}).idle_timeout_sec == DEFAULT_IDLE_TIMEOUT_SEC
    assert StdioLifecyclePolicy.from_environ({}).parent_watchdog is True


def test_tools_list_is_not_a_tool_call() -> None:
    assert request_method('{"jsonrpc":"2.0","id":1,"method":"tools/list"}') == "tools/list"
    assert request_method("{not-json") is None

    state = SessionState.start(0.0)
    state.note_method("initialize", 1.0)
    state.note_method("tools/list", 2.0)
    assert state.saw_tool_call is False
    assert (
        next_exit_reason(
            state,
            StdioLifecyclePolicy(unused_timeout_sec=10, idle_timeout_sec=30, parent_watchdog=False),
            now=10.0,
            parent_alive=True,
        )
        == "unused_timeout"
    )


def test_unused_timeout_does_not_fire_after_tools_call() -> None:
    state = SessionState.start(0.0)
    state.note_method("tools/call", 1.0)
    policy = StdioLifecyclePolicy(unused_timeout_sec=10, idle_timeout_sec=30, parent_watchdog=False)

    assert next_exit_reason(state, policy, now=15.0, parent_alive=True) is None
    assert next_exit_reason(state, policy, now=31.0, parent_alive=True) == "idle_timeout"


def test_parent_exit_beats_timeouts() -> None:
    state = SessionState.start(0.0)
    policy = StdioLifecyclePolicy(unused_timeout_sec=10, idle_timeout_sec=10, parent_watchdog=True)

    assert next_exit_reason(state, policy, now=1.0, parent_alive=False) == "parent_exited"
    assert (
        next_exit_reason(
            state,
            StdioLifecyclePolicy(unused_timeout_sec=10, idle_timeout_sec=10, parent_watchdog=False),
            now=1.0,
            parent_alive=False,
        )
        is None
    )


def test_zero_timeouts_disable_reaping() -> None:
    state = SessionState.start(0.0)
    policy = StdioLifecyclePolicy(unused_timeout_sec=None, idle_timeout_sec=None, parent_watchdog=False)

    assert next_exit_reason(state, policy, now=10_000.0, parent_alive=False) is None


def test_resolve_supervising_parent_skips_python_and_mcp_wrappers() -> None:
    table = {
        15132: ProcessInfo(15132, 4200, "python.exe"),
        4200: ProcessInfo(4200, 4376, "python.exe"),
        4376: ProcessInfo(4376, 8680, "tg-recall-mcp.exe"),
        8680: ProcessInfo(8680, 1872, "codex.exe"),
        1872: ProcessInfo(1872, 10704, "ChatGPT.exe"),
    }

    parent = resolve_supervising_parent(15132, table)

    assert parent is not None
    assert parent.pid == 8680
    assert parent.name == "codex.exe"


def test_resolve_supervising_parent_returns_none_when_parent_missing() -> None:
    table = {10: ProcessInfo(10, 99, "python.exe")}

    assert resolve_supervising_parent(10, table) is None


def test_snapshot_supervising_parent_accepts_an_injected_table() -> None:
    table = {
        11: ProcessInfo(11, 12, "python.exe"),
        12: ProcessInfo(12, 13, "tg-recall-mcp.exe"),
        13: ProcessInfo(13, 14, "codex.exe", created=99),
        14: ProcessInfo(14, 0, "ChatGPT.exe"),
    }

    snapshot = snapshot_supervising_parent(pid=11, table=table)

    assert snapshot == ParentSnapshot(pid=13, created=99, name="codex.exe")


def test_snapshot_supervising_parent_does_not_raise_for_the_live_process() -> None:
    snapshot_supervising_parent()


def test_serve_stdio_handles_a_request_then_eof() -> None:
    stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"initialize"}\n')
    stdout = io.StringIO()
    logs: list[str] = []
    policy = StdioLifecyclePolicy(unused_timeout_sec=None, idle_timeout_sec=None, parent_watchdog=False)

    code = serve_stdio(_handle, stdin=stdin, stdout=stdout, policy=policy, parent=None, log=logs.append)

    assert code == 0
    assert logs == []
    assert json.loads(stdout.getvalue()) == {"jsonrpc": "2.0", "id": 1, "result": {"method": "initialize"}}


def test_serve_stdio_exits_on_unused_timeout_while_stdin_stays_open() -> None:
    stdin, writer = _open_pipe()
    logs: list[str] = []
    policy = StdioLifecyclePolicy(unused_timeout_sec=0.05, idle_timeout_sec=None, parent_watchdog=False)
    try:
        started = time.monotonic()
        code = serve_stdio(_handle, stdin=stdin, stdout=io.StringIO(), policy=policy, parent=None, log=logs.append)
        elapsed = time.monotonic() - started
    finally:
        writer.close()
        stdin.close()

    assert code == 0
    assert logs == ["tg-recall-mcp: exiting (unused_timeout)"]
    assert elapsed < 2


def test_serve_stdio_idle_timeout_starts_after_tools_call() -> None:
    stdin, writer = _open_pipe()
    logs: list[str] = []
    policy = StdioLifecyclePolicy(unused_timeout_sec=None, idle_timeout_sec=0.05, parent_watchdog=False)
    try:
        writer.write('{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{}}\n')
        writer.flush()
        started = time.monotonic()
        code = serve_stdio(_handle, stdin=stdin, stdout=io.StringIO(), policy=policy, parent=None, log=logs.append)
        elapsed = time.monotonic() - started
    finally:
        writer.close()
        stdin.close()

    assert code == 0
    assert logs == ["tg-recall-mcp: exiting (idle_timeout)"]
    assert elapsed < 2


def test_serve_stdio_exits_when_parent_dies() -> None:
    stdin, writer = _open_pipe()
    logs: list[str] = []
    policy = StdioLifecyclePolicy(unused_timeout_sec=30, idle_timeout_sec=30, parent_watchdog=True)
    parent = ParentSnapshot(pid=8680, created=1, name="codex.exe")
    try:
        code = serve_stdio(
            _handle,
            stdin=stdin,
            stdout=io.StringIO(),
            policy=policy,
            parent=parent,
            parent_alive=lambda snapshot: False,
            log=logs.append,
        )
    finally:
        writer.close()
        stdin.close()

    assert code == 0
    assert logs == ["tg-recall-mcp: exiting (parent_exited)"]
