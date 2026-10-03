"""Benchmark how a real agent answers questions with tg-recall.

Each question runs through headless Claude Code (``claude -p``) that may use
only the tg-recall MCP tools and Read (for export files). Per question it
records turns, tool calls, the tokens tool results cost, total tokens, cost,
time, and whether the answer contains the expected keywords. Compare two runs
to see whether a change made agents cheaper or better.

Questions live in your profile, not in the repository, because they name
your chats: ``<profile data dir>/bench/questions.json`` (see
``scripts/bench_questions.example.json`` for the format). Results, including
the answers, are written next to it.

    uv run python scripts/agent_bench.py                 # run all questions
    uv run python scripts/agent_bench.py --only fact-outage --model sonnet
    uv run python scripts/agent_bench.py --compare old.json new.json

Running costs real tokens: every question is a full agent session.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROMPT_SUFFIX = (
    "\n\nОтвечай по архиву Telegram через инструменты tg-recall. Кратко, с датами; где уместно — ссылки tg://."
)
ALLOWED_TOOLS = ["mcp__tg-recall", "Read"]
DENIED_TOOLS = ["Bash", "Edit", "Write", "WebFetch", "WebSearch", "Task", "NotebookEdit"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--questions", help="questions JSON (default: <profile>/bench/questions.json)")
    parser.add_argument("--only", action="append", help="question id; repeatable")
    parser.add_argument("--model", help="model for the agent (default: your Claude Code default)")
    parser.add_argument(
        "--mcp-command", default="tg-recall-mcp", help="MCP server command, e.g. 'uv run --project . tg-recall-mcp'"
    )
    parser.add_argument("--max-budget-usd", type=float, default=2.0, help="per question")
    parser.add_argument("--timeout", type=int, default=600, help="seconds per question")
    parser.add_argument("--label", default="", help="added to the result file name")
    parser.add_argument(
        "--compare", nargs=2, metavar=("BEFORE", "AFTER"), help="compare two result files instead of running"
    )
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if args.compare:
        print(compare(*(json.loads(Path(path).read_text(encoding="utf-8")) for path in args.compare)))
        return 0

    from tg_recall import __version__
    from tg_recall.config import load_config

    config = load_config()
    bench_dir = Path(config.data_dir) / "bench"
    questions_path = Path(args.questions) if args.questions else bench_dir / "questions.json"
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    if args.only:
        questions = [question for question in questions if question["id"] in args.only]
    mcp_config = _mcp_config(args.mcp_command)
    results = []
    for question in questions:
        result = run_question(question, mcp_config, config.db_path, args)
        results.append(result)
        print(_row(result), flush=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = bench_dir / f"bench-{stamp}-v{__version__}{'-' + args.label if args.label else ''}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"version": __version__, "model": args.model, "results": results}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(_summary(results))
    print(f"results (with answers): {out}")
    return 0


def run_question(question: dict[str, Any], mcp_config: Path, db_path: str, args: argparse.Namespace) -> dict[str, Any]:
    command = [
        "claude", "-p", question["question"] + PROMPT_SUFFIX,
        "--output-format", "json",
        "--mcp-config", str(mcp_config), "--strict-mcp-config",
        "--allowedTools", *ALLOWED_TOOLS,
        "--disallowedTools", *DENIED_TOOLS,
        "--no-session-persistence",
        "--max-budget-usd", str(args.max_budget_usd),
    ]  # fmt: skip
    if args.model:
        command += ["--model", args.model]
    started_at = datetime.now(UTC).isoformat()
    started = time.monotonic()
    env = {key: value for key, value in os.environ.items() if key != "CLAUDECODE"}
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", timeout=args.timeout, env=env
        )
        output = json.loads(completed.stdout or "{}")
        error = None if completed.returncode == 0 else (completed.stderr or completed.stdout)[-300:]
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        output, error = {}, f"{type(exc).__name__}: {str(exc)[:200]}"
    seconds = time.monotonic() - started
    calls = _agent_calls(db_path, started_at)
    answer = str(output.get("result") or "")
    usage = output.get("usage") or {}
    missing = [word for word in question.get("must", []) if word.casefold() not in answer.casefold()]
    return {
        "id": question["id"],
        "kind": question.get("kind"),
        "ok": not error and not missing,
        "missing": missing,
        "error": error,
        "seconds": round(seconds, 1),
        "turns": output.get("num_turns"),
        "cost_usd": output.get("total_cost_usd"),
        "input_tokens": sum(
            int(usage.get(key) or 0)
            for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        ),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "tool_calls": [call["tool"] for call in calls],
        "tool_tokens": sum(call["tokens"] for call in calls),
        "tool_errors": [call["error"] for call in calls if call["error"]],
        "answer": answer,
        "expect": question.get("expect"),
    }


def _agent_calls(db_path: str, since: str) -> list[dict[str, Any]]:
    """MCP calls logged by tg-recall while this question ran (runs are sequential)."""

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT scope, details_json FROM audit_events WHERE event_type = 'agent_call' AND created_at >= ? ORDER BY id",
            (since,),
        ).fetchall()
    calls = []
    for tool, details_json in rows:
        details = json.loads(details_json or "{}")
        if details.get("surface") == "mcp":
            calls.append({"tool": tool, "tokens": int(details.get("tokens") or 0), "error": details.get("error")})
    return calls


def _mcp_config(command: str) -> Path:
    parts = command.split()
    config = {"mcpServers": {"tg-recall": {"command": parts[0], "args": parts[1:]}}}
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(config, handle)
    handle.close()
    return Path(handle.name)


def _row(result: dict[str, Any]) -> str:
    tools = ",".join(result["tool_calls"]) or "-"
    status = "ok" if result["ok"] else ("ERR" if result["error"] else f"miss {result['missing']}")
    cost = f"${result['cost_usd']:.3f}" if result["cost_usd"] is not None else "$?"
    return (
        f"{result['id']:<24} {status:<10} turns {result['turns'] or '?':>2} · tools {len(result['tool_calls']):>2} [{tools}] · "
        f"tool tok {result['tool_tokens']:>6} · in {result['input_tokens']:>7} · out {result['output_tokens']:>5} · {cost} · {result['seconds']}s"
    )


def _summary(results: list[dict[str, Any]]) -> str:
    def total(key: str) -> float:
        return sum(float(result[key] or 0) for result in results)

    ok = sum(1 for result in results if result["ok"])
    calls = sum(len(result["tool_calls"]) for result in results)
    return (
        f"\n{ok}/{len(results)} ok · {calls} tool calls · tool tok {int(total('tool_tokens'))} · "
        f"in {int(total('input_tokens'))} · out {int(total('output_tokens'))} · ${total('cost_usd'):.2f} · {total('seconds'):.0f}s"
    )


def compare(before: dict[str, Any], after: dict[str, Any]) -> str:
    old = {result["id"]: result for result in before["results"]}
    lines = [f"{before['version']} → {after['version']}", "id · ok · tool calls · tool tok · cost $ · seconds"]
    for result in after["results"]:
        previous = old.get(result["id"])
        if previous is None:
            continue

        def delta(key: str, value=lambda r, k: float(r[k] or 0)) -> str:
            return f"{value(previous, key):g}→{value(result, key):g}"

        calls = f"{len(previous['tool_calls'])}→{len(result['tool_calls'])}"
        lines.append(
            f"{result['id']} · {'ok' if previous['ok'] else 'no'}→{'ok' if result['ok'] else 'no'} · {calls} · "
            f"{delta('tool_tokens')} · {delta('cost_usd')} · {delta('seconds')}"
        )
    lines.append("before:" + _summary(before["results"]))
    lines.append("after: " + _summary(after["results"]))
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
