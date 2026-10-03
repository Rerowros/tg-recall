"""How agents actually use the archive: calls, tokens, latency, misses.

Every MCP call (and every CLI tool call from an agent shell) leaves an
``agent_call`` audit row with its arguments, output tokens, latency and error
code — never message text. This report turns them into the numbers worth
optimising: which tools cost the most, which searches come back empty, and
where an agent repeats itself.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from .storage import Database

REPEAT_SECONDS = 120


def usage_report(db: Database, *, since: str | None = None, client: str | None = None) -> str:
    rows = _calls(db, since, client)
    if not rows:
        return (
            "0 agent calls recorded"
            + (f" since {since}" if since else "")
            + " (MCP and agent-shell CLI calls are logged from v0.8)"
        )
    by_tool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_tool[row["tool"]].append(row)
    first, last = rows[0]["at"][:16].replace("T", " "), rows[-1]["at"][:16].replace("T", " ")
    clients = Counter(row["client"] for row in rows)
    lines = [
        f"{len(rows)} agent calls · {first} → {last} UTC · clients: "
        + ", ".join(f"{name} {count}" for name, count in clients.most_common(5)),
        "tool · calls · errors · empty · avg tok · max tok · avg ms",
    ]
    for tool, calls in sorted(by_tool.items(), key=lambda item: -len(item[1])):
        tokens = [call["tokens"] for call in calls if not call["error"]]
        times = [call["ms"] for call in calls]
        errors = sum(1 for call in calls if call["error"])
        empty = sum(1 for call in calls if not call["error"] and not call["count"])
        lines.append(
            f"{tool} · {len(calls)} · {errors} · {empty} · {_avg(tokens)} · {max(tokens, default=0)} · {_avg(times)}"
        )
    errors = Counter(call["error"] for call in rows if call["error"])
    if errors:
        lines.append("errors: " + " · ".join(f"{code} {count}" for code, count in errors.most_common(8)))
    misses = [call for call in rows if call["tool"] == "search" and not call["error"] and not call["count"]]
    if misses:
        queries = Counter(str(call["args"].get("query", "")) for call in misses)
        lines.append(
            "empty searches: "
            + " · ".join(f"{query!r}" + (f" ×{count}" if count > 1 else "") for query, count in queries.most_common(10))
        )
    identical, rephrased = _repeats(rows)
    if identical:
        lines.append(
            f"identical calls within {REPEAT_SECONDS}s: "
            + " · ".join(f"{tool} {count}" for tool, count in identical.most_common())
        )
    if rephrased:
        lines.append(f"searches retried after an empty result: {rephrased}")
    pages = sum(1 for call in rows if call["tool"] == "read" and call["args"].get("since") and call["count"] >= 100)
    if pages:
        lines.append(f"read() pages of 100+ msgs: {pages} (bulk reading; export() writes the period to one file)")
    return "\n".join(lines)


def _calls(db: Database, since: str | None, client: str | None) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = "event_type = 'agent_call'"
    if since:
        where += " AND created_at >= ?"
        params.append(since)
    with db.connect() as conn:
        raw = conn.execute(
            f"SELECT scope, details_json, created_at FROM audit_events WHERE {where} ORDER BY id", params
        ).fetchall()
    calls = []
    for row in raw:
        details = json.loads(row["details_json"] or "{}")
        if client and details.get("client") != client:
            continue
        calls.append(
            {
                "tool": row["scope"],
                "at": row["created_at"],
                "client": details.get("client") or "?",
                "args": details.get("args") or {},
                "tokens": int(details.get("tokens") or 0),
                "ms": int(details.get("ms") or 0),
                "count": int(details.get("count") or 0),
                "error": details.get("error"),
            }
        )
    return calls


def _repeats(rows: list[dict[str, Any]]) -> tuple[Counter[str], int]:
    """Signs the first answer did not suffice, per client within REPEAT_SECONDS.

    Identical: the same tool with the same arguments again. Rephrased: a
    search right after a search that found nothing.
    """

    identical: Counter[str] = Counter()
    rephrased = 0
    seen: dict[tuple[str, str, str], datetime] = {}
    last_search: dict[str, tuple[datetime, int]] = {}
    for row in rows:
        moment = datetime.fromisoformat(row["at"])
        key = (row["client"], row["tool"], json.dumps(row["args"], sort_keys=True, ensure_ascii=False))
        previous = seen.get(key)
        if previous is not None and (moment - previous).total_seconds() <= REPEAT_SECONDS:
            identical[row["tool"]] += 1
        seen[key] = moment
        if row["tool"] == "search":
            before = last_search.get(row["client"])
            if before is not None and not before[1] and (moment - before[0]).total_seconds() <= REPEAT_SECONDS:
                rephrased += 1
            last_search[row["client"]] = (moment, 0 if row["error"] else row["count"])
    return identical, rephrased


def _avg(values: list[int]) -> int:
    return round(sum(values) / len(values)) if values else 0
