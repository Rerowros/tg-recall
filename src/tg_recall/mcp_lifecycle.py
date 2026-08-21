"""Stdio MCP process lifecycle: unused/idle timeouts and parent-death reaping.

Hosts such as Codex spawn a new ``tg-recall-mcp`` per thread and, on Windows,
may keep stdin open after the thread is abandoned.  The server therefore
exits on its own rather than waiting forever for EOF.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO, cast


DEFAULT_UNUSED_TIMEOUT_SEC = 600.0
DEFAULT_IDLE_TIMEOUT_SEC = 1800.0
PARENT_POLL_INTERVAL_SEC = 1.0
MAX_TIMEOUT_SEC = 7 * 24 * 3600
UNUSED_TIMEOUT_ENV = "TG_RECALL_MCP_UNUSED_TIMEOUT_SEC"
IDLE_TIMEOUT_ENV = "TG_RECALL_MCP_IDLE_TIMEOUT_SEC"
PARENT_WATCHDOG_ENV = "TG_RECALL_MCP_PARENT_WATCHDOG"
TOOL_CALL_METHOD = "tools/call"
WRAPPER_PROCESS_NAMES = frozenset(
    {
        "python",
        "python.exe",
        "pythonw",
        "pythonw.exe",
        "python3",
        "python3.exe",
        "tg-recall-mcp",
        "tg-recall-mcp.exe",
        "tg-ecosystem-mcp",
        "tg-ecosystem-mcp.exe",
        "uv",
        "uv.exe",
    }
)
FALSE_ENV_VALUES = frozenset({"0", "false", "no", "off"})
TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
_SNAPSHOT_PARENT = object()


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    name: str
    created: int | None = None


@dataclass(frozen=True)
class ParentSnapshot:
    pid: int
    created: int | None = None
    name: str = ""


@dataclass(frozen=True)
class StdioLifecyclePolicy:
    unused_timeout_sec: float | None = DEFAULT_UNUSED_TIMEOUT_SEC
    idle_timeout_sec: float | None = DEFAULT_IDLE_TIMEOUT_SEC
    parent_watchdog: bool = True

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> "StdioLifecyclePolicy":
        env = os.environ if environ is None else environ
        return cls(
            unused_timeout_sec=parse_timeout_seconds(env.get(UNUSED_TIMEOUT_ENV), DEFAULT_UNUSED_TIMEOUT_SEC),
            idle_timeout_sec=parse_timeout_seconds(env.get(IDLE_TIMEOUT_ENV), DEFAULT_IDLE_TIMEOUT_SEC),
            parent_watchdog=parse_bool_env(env.get(PARENT_WATCHDOG_ENV), default=True),
        )


@dataclass
class SessionState:
    started_at: float
    last_activity_at: float
    saw_tool_call: bool = False

    @classmethod
    def start(cls, now: float) -> "SessionState":
        return cls(started_at=now, last_activity_at=now)

    def note_method(self, method: str | None, now: float) -> None:
        self.last_activity_at = now
        if method == TOOL_CALL_METHOD:
            self.saw_tool_call = True


def parse_timeout_seconds(raw: str | None, default: float) -> float | None:
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        return default
    if value <= 0:
        return None
    return min(value, float(MAX_TIMEOUT_SEC))


def parse_bool_env(raw: str | None, default: bool) -> bool:
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in FALSE_ENV_VALUES:
        return False
    if value in TRUE_ENV_VALUES:
        return True
    return default


def request_method(line: str) -> str | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    method = payload.get("method")
    return method if isinstance(method, str) else None


def next_exit_reason(
    state: SessionState,
    policy: StdioLifecyclePolicy,
    now: float,
    parent_alive: bool,
) -> str | None:
    if policy.parent_watchdog and not parent_alive:
        return "parent_exited"
    if not state.saw_tool_call and policy.unused_timeout_sec is not None:
        if now - state.started_at >= policy.unused_timeout_sec:
            return "unused_timeout"
    if policy.idle_timeout_sec is not None and now - state.last_activity_at >= policy.idle_timeout_sec:
        return "idle_timeout"
    return None


def seconds_until_next_check(state: SessionState, policy: StdioLifecyclePolicy, now: float) -> float | None:
    waits: list[float] = []
    if not state.saw_tool_call and policy.unused_timeout_sec is not None:
        waits.append(state.started_at + policy.unused_timeout_sec - now)
    if policy.idle_timeout_sec is not None:
        waits.append(state.last_activity_at + policy.idle_timeout_sec - now)
    if policy.parent_watchdog:
        waits.append(PARENT_POLL_INTERVAL_SEC)
    if not waits:
        return None
    return max(0.0, min(waits))


def resolve_supervising_parent(pid: int, table: Mapping[int, ProcessInfo]) -> ProcessInfo | None:
    seen: set[int] = set()
    current = pid
    while current not in seen:
        seen.add(current)
        info = table.get(current)
        if info is None:
            return None
        parent = table.get(info.ppid)
        if parent is None or parent.pid == info.pid:
            return None
        if parent.name.lower() not in WRAPPER_PROCESS_NAMES:
            return parent
        current = parent.pid
    return None


def snapshot_supervising_parent(
    pid: int | None = None,
    table: Mapping[int, ProcessInfo] | None = None,
) -> ParentSnapshot | None:
    current_pid = os.getpid() if pid is None else pid
    resolved_table = table if table is not None else _process_table()
    parent = resolve_supervising_parent(current_pid, resolved_table)
    if parent is None or not _is_watchable_pid(parent.pid):
        return None
    created = parent.created if parent.created is not None else _process_created(parent.pid)
    return ParentSnapshot(pid=parent.pid, created=created, name=parent.name)


def process_is_alive(snapshot: ParentSnapshot) -> bool:
    inspected = _inspect_process(snapshot.pid)
    if inspected is None:
        return False
    alive, created = inspected
    if not alive:
        return False
    if snapshot.created is not None and created is not None and created != snapshot.created:
        return False
    return True


def serve_stdio(
    handle: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    policy: StdioLifecyclePolicy | None = None,
    parent: ParentSnapshot | None | object = _SNAPSHOT_PARENT,
    clock: Callable[[], float] = time.monotonic,
    parent_alive: Callable[[ParentSnapshot], bool] = process_is_alive,
    log: Callable[[str], None] | None = None,
) -> int:
    selected_policy = StdioLifecyclePolicy.from_environ() if policy is None else policy
    selected_parent: ParentSnapshot | None
    if parent is _SNAPSHOT_PARENT:
        selected_parent = snapshot_supervising_parent() if selected_policy.parent_watchdog else None
    else:
        selected_parent = cast(ParentSnapshot | None, parent)
    reason = _run_stdio_loop(
        handle,
        stdin=stdin,
        stdout=stdout,
        policy=selected_policy,
        parent=selected_parent,
        clock=clock,
        parent_alive=parent_alive,
    )
    if reason != "eof":
        writer = log or _default_log
        writer(f"tg-recall-mcp: exiting ({reason})")
    return 0


def _run_stdio_loop(
    handle: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    stdin: TextIO,
    stdout: TextIO,
    policy: StdioLifecyclePolicy,
    parent: ParentSnapshot | None,
    clock: Callable[[], float],
    parent_alive: Callable[[ParentSnapshot], bool],
) -> str:
    state = SessionState.start(clock())
    incoming: queue.Queue[str | None] = queue.Queue()
    reader = threading.Thread(target=_read_stdin, args=(stdin, incoming), name="tg-recall-mcp-stdin", daemon=True)
    reader.start()
    while True:
        alive = True if parent is None else parent_alive(parent)
        reason = next_exit_reason(state, policy, clock(), alive)
        if reason is not None:
            return reason
        wait = seconds_until_next_check(state, policy, clock())
        try:
            line = incoming.get() if wait is None else incoming.get(timeout=wait)
        except queue.Empty:
            continue
        if line is None:
            return "eof"
        if not line.strip():
            continue
        state.note_method(request_method(line), clock())
        response = handle(json.loads(line))
        print(json.dumps(response, ensure_ascii=False), flush=True, file=stdout)


def _read_stdin(stdin: TextIO, incoming: queue.Queue[str | None]) -> None:
    try:
        for line in stdin:
            incoming.put(line)
    finally:
        incoming.put(None)


def _default_log(message: str) -> None:
    print(message, file=sys.stderr)


def _is_watchable_pid(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return pid > 4
    return pid > 1


def _process_table() -> dict[int, ProcessInfo]:
    if os.name == "nt":
        return {item.pid: item for item in _windows_processes()}
    if Path("/proc/self/stat").exists():
        return _procfs_table(os.getpid())
    return _fallback_parent_table()


def _process_created(pid: int) -> int | None:
    inspected = _inspect_process(pid)
    return None if inspected is None else inspected[1]


def _inspect_process(pid: int) -> tuple[bool, int | None] | None:
    if os.name == "nt":
        return _windows_inspect(pid)
    return _posix_inspect(pid)


def _fallback_parent_table() -> dict[int, ProcessInfo]:
    pid = os.getpid()
    ppid = os.getppid()
    return {
        pid: ProcessInfo(pid=pid, ppid=ppid, name="python"),
        ppid: ProcessInfo(pid=ppid, ppid=0, name="parent"),
    }


def _procfs_table(pid: int) -> dict[int, ProcessInfo]:
    table: dict[int, ProcessInfo] = {}
    current = pid
    seen: set[int] = set()
    while current > 0 and current not in seen:
        seen.add(current)
        info = _procfs_process(current)
        if info is None:
            break
        table[info.pid] = info
        current = info.ppid
    return table


def _procfs_process(pid: int) -> ProcessInfo | None:
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        text = stat_path.read_text(encoding="utf-8")
    except OSError:
        return None
    comm_start = text.find("(")
    comm_end = text.rfind(")")
    if comm_start < 0 or comm_end < comm_start:
        return None
    rest = text[comm_end + 2 :].split()
    if len(rest) < 20:
        return None
    try:
        ppid = int(rest[1])
        created = int(rest[19])
    except ValueError:
        return None
    name = text[comm_start + 1 : comm_end]
    comm_path = Path(f"/proc/{pid}/comm")
    try:
        name = comm_path.read_text(encoding="utf-8").strip() or name
    except OSError:
        pass
    return ProcessInfo(pid=pid, ppid=ppid, name=name, created=created)


def _posix_inspect(pid: int) -> tuple[bool, int | None] | None:
    if not _is_watchable_pid(pid):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False, None
    except PermissionError:
        created = None
        if Path(f"/proc/{pid}/stat").exists():
            info = _procfs_process(pid)
            created = None if info is None else info.created
        return True, created
    except OSError:
        return None
    created = None
    if Path(f"/proc/{pid}/stat").exists():
        info = _procfs_process(pid)
        created = None if info is None else info.created
    return True, created


def _windows_processes() -> list[ProcessInfo]:
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = (
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    invalid = ctypes.c_void_p(-1).value
    if not snapshot or snapshot == invalid:
        return []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    processes: list[ProcessInfo] = []
    try:
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return []
        while True:
            processes.append(
                ProcessInfo(
                    pid=int(entry.th32ProcessID),
                    ppid=int(entry.th32ParentProcessID),
                    name=entry.szExeFile,
                )
            )
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return processes


def _windows_inspect(pid: int) -> tuple[bool, int | None] | None:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        if ctypes.get_last_error() == ERROR_ACCESS_DENIED:
            return True, None
        return None
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return None
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        created = None
        if kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            created = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        return exit_code.value == STILL_ACTIVE, created
    finally:
        kernel32.CloseHandle(handle)
