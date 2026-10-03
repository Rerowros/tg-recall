"""The agent-facing archive tools (search / read / chats) behind MCP.

Each call resolves the AI access policy first, then queries only the
permitted chats and returns compact text sized to a token budget.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Sequence

from .agent_query import (
    AgentMessage,
    AgentQueryError,
    Scope,
    archive_synced_at,
    chat_titles,
    context_window,
    count_messages,
    fetch_messages,
    list_chats,
    load_cursor,
    parse_ref,
    parse_when,
    period_messages,
    resolve_sender,
    resolve_targets,
    save_cursor,
    search_hits,
    self_user_id,
    topic_titles,
)
from .agent_render import RenderContext, ago, citations_line, render_chat_list, render_messages, token_estimate, tz_label
from .config import AppConfig
from .security import AgentOperation, AgentPolicyError, PolicyDecision, RequestedAgentScope, audit_policy_decision, require_agent_policy
from .storage import Database

MEDIA_CHOICES = ("voice", "audio", "photo", "video", "document", "any")
MAX_BUDGET = 20000
AUTO_REFRESH_SECONDS = 8.0
AUTO_REFRESH_CHATS = 10
NEW_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class ToolResult:
    text: str
    count: int
    chat_ids: tuple[int, ...]


def allowed_chat_ids(config: AppConfig, db: Database) -> tuple[int, ...]:
    """Chats agents may see: the allowlist, or every archived chat with allow_all_chats."""

    policy = config.ai_access
    if policy.allow_all_chats:
        with db.connect() as conn:
            return tuple(row[0] for row in conn.execute("SELECT chat_id FROM chats ORDER BY chat_id"))
    return tuple(dict.fromkeys(int(value) for value in policy.allowed_chat_ids))


class AgentTools:
    """search / read / chats / sync over the archive.

    Agents (MCP, CLI in an agent shell) see only ``ai_access`` chats and
    policy bounds. ``owner=True`` is the human at a terminal: every archived
    chat, no policy, no auto-refresh.
    """

    def __init__(
        self,
        config: AppConfig,
        db: Database,
        *,
        client: str = "unknown",
        owner: bool = False,
        now: Callable[[], datetime] | None = None,
    ):
        self.config = config
        self.db = db
        self.client = client
        self.owner = owner
        self._now = now or (lambda: datetime.now(UTC))
        self._refresh_note: str | None = None

    # ------------------------------------------------------------ tools

    def chats(self, args: dict[str, Any]) -> ToolResult:
        decision = self._decide(AgentOperation.METADATA_LIST, None, None, None, None)
        found = list_chats(self.db, self._allowed(), args.get("query"))
        if self.owner and not args.get("all"):
            found = [item for item in found if item.messages] or found
        return ToolResult(render_chat_list(found, self._now()), len(found), tuple(item.chat_id for item in found) or decision.chat_ids)

    def sync(self, args: dict[str, Any]) -> ToolResult:
        """Download chats or forum topics from Telegram into the archive.

        No chats: every allowed chat (owner: allowlist, else every archived
        chat). Without since a known chat gets only new messages and an
        unknown one its last 30 days.
        """

        policy = self.config.ai_access
        if not self.owner and not policy.allow_sync:
            raise AgentQueryError("sync is off; the owner enables it with `tg-recall config set ai_access.allow_sync true`")
        spec = args.get("chats", args.get("chat_id"))
        if spec in (None, "", []):
            chats, topics = self._tracked(), ()
            if not chats:
                raise AgentQueryError("sync: no chats to update; pass chats or set ai_access.allowed_chat_ids")
        else:
            chats, topics = resolve_targets(self.db, self._allowed(), spec)
        since = parse_when(args.get("since"), now=self._now())
        decision = self._decide(AgentOperation.SYNC, chats, since, None, None)
        bounds = [datetime.fromisoformat(value) for value in (since, parse_when(decision.since, now=self._now())) if value]
        since_dt = max(bounds) if bounds else None
        targets: list[tuple[int, int | None]] = []
        for chat_id in decision.chat_ids:
            chat_topics: list[int | None] = [topic_id for topic_chat, topic_id in topics if topic_chat == chat_id]
            targets.extend((chat_id, topic_id) for topic_id in chat_topics or [None])
        seconds = float(args.get("max_seconds") or policy.sync_max_seconds)
        media = str(args.get("media") or "none") if self.owner else "none"
        from .telegram_client import TelegramArchiveClient

        results = asyncio.run(TelegramArchiveClient(self.config, self.db).sync_many(targets, since=since_dt, max_seconds=seconds, media=media))
        titles = chat_titles(self.db, decision.chat_ids)
        topic_names = topic_titles(self.db, decision.chat_ids)
        lines: list[str] = []
        for result in results:
            chat_id, topic_id = result["chat_id"], result["topic_id"]
            name = titles.get(chat_id) or str(chat_id)
            if topic_id is not None:
                name += f" /{topic_id} {topic_names.get(chat_id, {}).get(topic_id, '')}".rstrip()
            span = f"{_day(result['oldest_date'])} → {_day(result['newest_date'])}" if result["stored"] else "empty"
            state = "complete" if result["complete"] else (
                f"Telegram rate limit until {result['retry_after']}" if result["retry_after"] else "partial (time limit) → sync again"
            )
            lines.append(f"{name}: +{result['fetched']} · {result['stored']} stored ({span}) · {state}")
        skipped = len(targets) - len(results)
        if skipped:
            lines.append(f"{skipped} more chats not reached (rate limit) → sync again later")
        total = sum(result["fetched"] for result in results)
        partial = skipped or any(not result["complete"] for result in results)
        header = f"sync · {total} new msgs · {len(results)} chats" + (" · partial" if partial else "")
        return ToolResult(_assemble(header, "\n".join(lines), []), total, decision.chat_ids)

    def search(self, args: dict[str, Any]) -> ToolResult:
        return self._with_refresh_note(self._search(args))

    def read(self, args: dict[str, Any]) -> ToolResult:
        return self._with_refresh_note(self._read(args))

    def _with_refresh_note(self, result: ToolResult) -> ToolResult:
        if not self._refresh_note:
            return result
        return replace(result, text=f"{result.text}\n{self._refresh_note}")

    def _search(self, args: dict[str, Any]) -> ToolResult:
        query = str(args.get("query", "")).strip()
        if not query:
            raise AgentQueryError("search: query is required")
        context = _bounded_int(args, "context", 2, 0, 8)
        budget = _bounded_int(args, "budget", 4000, 200, MAX_BUDGET)
        scope, decision = self._scope(args, result_limit=_bounded_int(args, "limit", 10, 1, 50))
        limit = decision.result_limit or 10
        keys = search_hits(self.db, scope, query, limit)
        if not keys:
            return ToolResult(self._no_hits(scope), 0, scope.chat_ids)
        hits = fetch_messages(self.db, scope, keys)
        ordered = [hits[key] for key in keys if key in hits]
        ctx = self._render_context(scope.chat_ids, full=False)

        def build(radius: int, kept: Sequence[AgentMessage]) -> str:
            groups = self._windows(scope, kept, radius, radius)
            body = render_messages(groups, ctx)
            return body + "\n" + citations_line(kept)

        radius = context
        kept: list[AgentMessage] = list(ordered)
        text = build(radius, kept)
        while token_estimate(text) > budget and radius > 0:
            radius -= 1
            text = build(radius, kept)
        while token_estimate(text) > budget and len(kept) > 1:
            kept.pop()
            text = build(radius, kept)
        notes = []
        if radius < context:
            notes.append(f"context trimmed to ±{radius} (budget)")
        if len(kept) < len(ordered):
            notes.append(f"+{len(ordered) - len(kept)} hits omitted (budget) → raise budget or narrow chats/since")
        elif len(ordered) >= limit and decision.result_limit is not None and decision.result_limit < _bounded_int(args, "limit", 10, 1, 50):
            notes.append(f"capped at {decision.result_limit} hits by ai_access.max_results")
        chats = len({message.chat_id for message in kept})
        header = f"{len(kept)} hits · {chats} chats · tz {tz_label()} · archive synced {ago(archive_synced_at(self.db, scope.chat_ids), self._now())}"
        return ToolResult(_assemble(header, text, notes), len(kept), scope.chat_ids)

    def _read(self, args: dict[str, Any]) -> ToolResult:
        refs = args.get("refs") or []
        if isinstance(refs, str):
            refs = [refs]
        if refs:
            return self._read_refs(refs, args)
        if args.get("since") or args.get("until"):
            return self._read_period(args)
        if args.get("chats") not in (None, "", []):
            return self._read_recent(args)
        return self._read_new(args)

    # ------------------------------------------------------------ read modes

    def _read_refs(self, refs: Sequence[Any], args: dict[str, Any]) -> ToolResult:
        if len(refs) > 8:
            raise AgentQueryError("read: at most 8 refs per call")
        parsed = [parse_ref(ref) for ref in refs]
        chat_ids = tuple(dict.fromkeys(chat_id for chat_id, _ in parsed))
        before = _bounded_int(args, "before", 3, 0, 50)
        after = _bounded_int(args, "after", 3, 0, 50)
        budget = _bounded_int(args, "budget", 6000, 200, MAX_BUDGET)
        full = bool(args.get("full", False))
        scope, _ = self._scope({**args, "chats": list(chat_ids)})
        anchors = fetch_messages(self.db, scope, parsed)
        missing = [f"{chat_id}/{message_id}" for chat_id, message_id in parsed if (chat_id, message_id) not in anchors]
        ctx = self._render_context(scope.chat_ids, full=full)
        kept = [anchors[key] for key in parsed if key in anchors]
        text = render_messages(self._windows(scope, kept, before, after), ctx)
        while token_estimate(text) > budget and (before or after):
            before, after = max(0, before - 1), max(0, after - 1)
            text = render_messages(self._windows(scope, kept, before, after), ctx)
        notes = [f"not found or outside the allowed scope: {', '.join(missing)}"] if missing else []
        header = f"{len(kept)} refs · window -{before}/+{after} · tz {tz_label()}"
        return ToolResult(_assemble(header, text, notes), len(kept), scope.chat_ids)

    def _read_period(self, args: dict[str, Any]) -> ToolResult:
        scope, _ = self._scope(args)
        cap = self._read_cap(args)
        budget = _bounded_int(args, "budget", 6000, 200, MAX_BUDGET)
        rows = period_messages(self.db, scope, limit=cap + 1)
        more = len(rows) > cap
        rows = rows[:cap]
        rows, cut = self._fit_chronological(rows, budget, scope.chat_ids)
        notes = []
        if more or cut:
            remaining = sum(count_messages(self.db, replace(scope, since=_after(rows[-1].date))).values()) if rows else 0
            notes.append(f"+{remaining} more → read(since='{rows[-1].date}') with the same chats" if rows else "budget too small")
        header = f"{len(rows)} msgs · {len({row.chat_id for row in rows})} chats · {_period_label(scope)} · tz {tz_label()}"
        return ToolResult(_assemble(header, self._render_rows(rows, scope.chat_ids), notes), len(rows), scope.chat_ids)

    def _read_recent(self, args: dict[str, Any]) -> ToolResult:
        scope, _ = self._scope(args)
        cap = min(self._read_cap(args), _bounded_int(args, "limit", 50, 1, 1000))
        budget = _bounded_int(args, "budget", 6000, 200, MAX_BUDGET)
        newest = period_messages(self.db, scope, limit=cap, newest_first=True)
        rows = list(reversed(newest))
        dropped = 0
        while rows and token_estimate(self._render_rows(rows, scope.chat_ids)) > budget:
            rows.pop(0)
            dropped += 1
        notes = [f"{dropped} older msgs dropped (budget) → read(chats, since=…) for a full period"] if dropped else []
        header = f"last {len(rows)} msgs · {len({row.chat_id for row in rows})} chats · tz {tz_label()} · archive synced {ago(archive_synced_at(self.db, scope.chat_ids), self._now())}"
        return ToolResult(_assemble(header, self._render_rows(rows, scope.chat_ids), notes), len(rows), scope.chat_ids)

    def _read_new(self, args: dict[str, Any]) -> ToolResult:
        """Newest messages since this client's cursor, a fair share per chat.

        One noisy chat must not crowd out the rest: each chat gets an equal
        slice of the cap (unused slices go to busier chats), keeps its newest
        messages, and the cursor jumps to what was shown. Older skipped
        messages are counted with a hint to read the whole period.
        """

        scope, _ = self._scope(args)
        cap = self._read_cap(args)
        budget = _bounded_int(args, "budget", 6000, 200, MAX_BUDGET)
        cursor = load_cursor(self.db, self.config.profile, self.client, scope.chat_ids)
        fresh_since = _later(scope.since, (self._now() - NEW_WINDOW).isoformat())
        newest: dict[int, list[AgentMessage]] = {}
        pending: dict[int, int] = {}
        for chat_id in scope.chat_ids:
            if chat_id in cursor:
                chat_scope, after = replace(scope, chat_ids=(chat_id,)), {chat_id: cursor[chat_id]}
            else:
                chat_scope, after = replace(scope, chat_ids=(chat_id,), since=fresh_since), None
            rows = period_messages(self.db, chat_scope, after_ids=after, limit=cap, newest_first=True)
            if rows:
                newest[chat_id] = rows
                pending[chat_id] = count_messages(self.db, chat_scope, after_ids=after).get(chat_id, len(rows))
        shares = _fair_shares({chat_id: len(rows) for chat_id, rows in newest.items()}, cap)
        order = sorted(newest, key=lambda chat_id: (newest[chat_id][0].date, chat_id), reverse=True)

        def pick(level: int) -> list[AgentMessage]:
            picked: list[AgentMessage] = []
            for chat_id in order:
                picked.extend(reversed(newest[chat_id][: min(shares[chat_id], level)]))
            return picked

        level = max(shares.values(), default=0)
        rows = pick(level)
        if rows and token_estimate(self._render_rows(rows, scope.chat_ids)) > budget:
            low, high = 1, level
            while low < high:
                middle = (low + high + 1) // 2
                if token_estimate(self._render_rows(pick(middle), scope.chat_ids)) <= budget:
                    low = middle
                else:
                    high = middle - 1
            rows = pick(low)
        shown: dict[int, int] = {}
        per_chat: dict[int, int] = {}
        for row in rows:
            shown[row.chat_id] = max(shown.get(row.chat_id, 0), row.message_id)
            per_chat[row.chat_id] = per_chat.get(row.chat_id, 0) + 1
        save_cursor(self.db, self.config.profile, self.client, shown, self._now().isoformat())
        notes = []
        skipped = {chat_id: pending[chat_id] - per_chat.get(chat_id, 0) for chat_id in newest}
        skipped = {chat_id: count for chat_id, count in skipped.items() if count > 0}
        if skipped:
            titles = chat_titles(self.db, list(skipped))
            listed = sorted(skipped, key=lambda chat_id: skipped[chat_id], reverse=True)
            parts = ", ".join(f"{titles.get(chat_id) or chat_id} +{skipped[chat_id]}" for chat_id in listed[:6])
            more = f", +{len(listed) - 6} chats" if len(listed) > 6 else ""
            notes.append(f"older new msgs skipped: {parts}{more} → read(chats=…, since=…) for the whole period")
        quiet = [chat_id for chat_id in scope.chat_ids if chat_id not in newest]
        if quiet:
            titles = chat_titles(self.db, quiet[:6])
            names = ", ".join(titles.get(chat_id) or str(chat_id) for chat_id in quiet[:6])
            notes.append(f"no new: {names}" + (f", +{len(quiet) - 6}" if len(quiet) > 6 else ""))
        notes.append("cursor saved; first read covers the last 24h")
        header = f"{len(rows)} new msgs · {len(shown)} chats · tz {tz_label()} · archive synced {ago(archive_synced_at(self.db, scope.chat_ids), self._now())}"
        return ToolResult(_assemble(header, self._render_rows(rows, scope.chat_ids), notes), len(rows), scope.chat_ids)

    # ------------------------------------------------------------ helpers

    def _allowed(self) -> tuple[int, ...]:
        if self.owner:
            with self.db.connect() as conn:
                return tuple(row[0] for row in conn.execute("SELECT chat_id FROM chats ORDER BY chat_id"))
        return allowed_chat_ids(self.config, self.db)

    def _tracked(self) -> tuple[int, ...]:
        """Chats `sync` updates by default: the allowlist, else every chat with messages."""

        policy = self.config.ai_access
        allowed = tuple(dict.fromkeys(int(value) for value in policy.allowed_chat_ids))
        if not policy.allow_all_chats and (allowed or not self.owner):
            return allowed
        with self.db.connect() as conn:
            return tuple(row[0] for row in conn.execute("SELECT DISTINCT chat_id FROM messages ORDER BY chat_id"))

    def _auto_refresh(self, chat_ids: Sequence[int]) -> None:
        """Pull new messages for stale chats before an agent reads them (allow_sync only)."""

        policy = self.config.ai_access
        minutes = int(policy.auto_refresh_minutes)
        if self.owner or not policy.allow_sync or minutes <= 0 or not chat_ids:
            return
        cutoff = (self._now() - timedelta(minutes=minutes)).isoformat()
        marks = ", ".join("?" for _ in chat_ids)
        with self.db.connect() as conn:
            stale = [
                row[0]
                for row in conn.execute(
                    f"SELECT DISTINCT m.chat_id FROM messages m LEFT JOIN sync_state s ON s.chat_id = m.chat_id "
                    f"WHERE m.chat_id IN ({marks}) AND (s.last_synced_at IS NULL OR s.last_synced_at < ?)",
                    [*chat_ids, cutoff],
                )
            ]
        if not stale:
            return
        from .telegram_client import TelegramArchiveClient

        try:
            results = asyncio.run(
                TelegramArchiveClient(self.config, self.db).sync_many([(chat_id, None) for chat_id in stale[:AUTO_REFRESH_CHATS]], max_seconds=AUTO_REFRESH_SECONDS)
            )
            fetched = sum(result["fetched"] for result in results)
            self._refresh_note = f"refreshed {len(results)} chats (+{fetched})"
        except Exception as exc:
            # Reading the archive as it is beats failing the call (busy session, rate limit, offline).
            self._refresh_note = f"refresh skipped: {str(exc).split(';')[0][:80]}"

    def _scope(self, args: dict[str, Any], *, result_limit: int | None = None) -> tuple[Scope, PolicyDecision]:
        policy = self.config.ai_access
        allowed = self._allowed()
        if not self.owner and (not policy.enabled or not allowed):
            self._decide(AgentOperation.ARCHIVE_READ, allowed, None, None, None)
        chats, topics = resolve_targets(self.db, allowed, args.get("chats", args.get("chat_id")))
        since = parse_when(args.get("since"), now=self._now())
        until = parse_when(args.get("until"), end=True, now=self._now())
        if since and until and since > until:
            raise AgentQueryError("since is after until")
        media = args.get("media")
        if media is not None and media not in MEDIA_CHOICES:
            raise AgentQueryError(f"media must be one of {', '.join(MEDIA_CHOICES)}")
        decision = self._decide(AgentOperation.ARCHIVE_READ, chats, since, until, result_limit)
        self._auto_refresh(decision.chat_ids)
        sender_ids, sender_name = resolve_sender(self.db, args.get("from"))
        allowed_media = _policy_media(decision.media_policy)
        scope = Scope(
            chat_ids=tuple(decision.chat_ids),
            since=_later(since, decision.since),
            until=_earlier(until, decision.until),
            media_types=None if media is None else (() if media == "any" else (media,)),
            allowed_media=allowed_media,
            sender_ids=sender_ids,
            sender_name=sender_name,
            topics=tuple(pair for pair in topics if pair[0] in decision.chat_ids),
        )
        return scope, decision

    def _decide(self, operation: AgentOperation, chats: Sequence[int] | None, since: str | None, until: str | None, limit: int | None) -> PolicyDecision:
        if self.owner:
            return PolicyDecision(operation=operation, allowed=True, chat_ids=tuple(chats or ()), since=since, until=until, result_limit=limit)
        policy = self.config.ai_access
        requested = RequestedAgentScope(
            chat_ids=tuple(chats or ()), since=since, until=until, media_policy=None, result_limit=limit
        )
        try:
            decision = require_agent_policy(
                operation,
                enabled=policy.enabled,
                allowed_chat_ids=list(self._allowed()),
                max_results=policy.max_results,
                allowed_since=policy.allowed_since,
                allowed_until=policy.allowed_until,
                allowed_media_types=policy.allowed_media_types,
                requested=requested,
                automation=True,
            )
        except AgentPolicyError as exc:
            _best_effort(audit_policy_decision, self.db, exc.decision, requested=requested)
            raise
        _best_effort(audit_policy_decision, self.db, decision, requested=requested)
        return decision

    def _read_cap(self, args: dict[str, Any]) -> int:
        ceiling = max(1, int(self.config.ai_access.max_read_messages))
        return min(ceiling, _bounded_int(args, "limit", 100, 1, 1000))

    def _render_context(self, chat_ids: Sequence[int], *, full: bool) -> RenderContext:
        return RenderContext(
            titles=chat_titles(self.db, chat_ids),
            self_id=self_user_id(self.db),
            full=full,
            now=self._now(),
            topics=topic_titles(self.db, chat_ids),
        )

    def _render_rows(self, rows: Sequence[AgentMessage], chat_ids: Sequence[int]) -> str:
        if not rows:
            return ""
        order: list[int] = []
        grouped: dict[int, list[AgentMessage | None]] = {}
        for row in rows:
            if row.chat_id not in grouped:
                order.append(row.chat_id)
                grouped[row.chat_id] = []
            grouped[row.chat_id].append(row)
        for chat_id, items in grouped.items():
            # Forum topics interleave in time; one block per topic reads as a conversation.
            first_seen: dict[int | None, int] = {}
            for index, item in enumerate(items):
                if item is not None:
                    first_seen.setdefault(item.topic_id, index)
            grouped[chat_id] = sorted(items, key=lambda item: first_seen[item.topic_id] if item is not None else 0)
        return render_messages([(chat_id, grouped[chat_id]) for chat_id in order], self._render_context(chat_ids, full=False))

    def _fit_chronological(self, rows: list[AgentMessage], budget: int, chat_ids: Sequence[int]) -> tuple[list[AgentMessage], bool]:
        """Keep the earliest rows that fit, so the next call continues from there."""

        if not rows or token_estimate(self._render_rows(rows, chat_ids)) <= budget:
            return rows, False
        low, high = 0, len(rows)
        while low < high:
            middle = (low + high + 1) // 2
            if token_estimate(self._render_rows(rows[:middle], chat_ids)) <= budget:
                low = middle
            else:
                high = middle - 1
        return rows[: max(low, 1)], True

    def _windows(self, scope: Scope, anchors: Sequence[AgentMessage], before: int, after: int) -> list[tuple[int, list[AgentMessage | None]]]:
        """Context around anchors, merged per chat, chats in anchor order."""

        hit_keys = {(item.chat_id, item.message_id) for item in anchors}
        per_chat: dict[int, list[list[AgentMessage]]] = {}
        order: list[int] = []
        for anchor in anchors:
            window = context_window(self.db, scope, anchor.chat_id, anchor.message_id, before, after) or [anchor]
            if anchor.chat_id not in per_chat:
                per_chat[anchor.chat_id] = []
                order.append(anchor.chat_id)
            per_chat[anchor.chat_id].append(window)
        groups: list[tuple[int, list[AgentMessage | None]]] = []
        for chat_id in order:
            windows = sorted(per_chat[chat_id], key=lambda window: window[0].message_id)
            merged: list[list[AgentMessage]] = []
            for window in windows:
                if merged and window[0].message_id <= merged[-1][-1].message_id:
                    known = {item.message_id for item in merged[-1]}
                    merged[-1].extend(item for item in window if item.message_id not in known)
                    merged[-1].sort(key=lambda item: item.message_id)
                else:
                    merged.append(list(window))
            flat: list[AgentMessage | None] = []
            for index, window in enumerate(merged):
                if index:
                    flat.append(None)
                flat.extend(replace(item, hit=(item.chat_id, item.message_id) in hit_keys) for item in window)
            groups.append((chat_id, flat))
        return groups

    def _no_hits(self, scope: Scope) -> str:
        synced = ago(archive_synced_at(self.db, scope.chat_ids), self._now())
        return f"0 hits in {len(scope.chat_ids)} chats ({_period_label(scope)}; archive synced {synced}) → try other words, widen since/chats"


def _assemble(header: str, body: str, notes: Sequence[str]) -> str:
    parts = [header]
    if body:
        parts.append(body.rstrip("\n"))
    parts.extend(notes)
    text = "\n".join(parts)
    return text.replace(header, f"{header} · ~{token_estimate(text)} tok", 1)


def _bounded_int(args: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = args.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise AgentQueryError(f"{key} must be an integer") from exc
    return max(low, min(high, value))


def _fair_shares(available: dict[int, int], cap: int) -> dict[int, int]:
    """Max-min fair split of ``cap`` across chats; quiet chats free their slice."""

    shares = {chat_id: 0 for chat_id in available}
    left = cap
    ordered = sorted(available, key=lambda chat_id: available[chat_id])
    for index, chat_id in enumerate(ordered):
        if left <= 0:
            break
        fair = max(1, left // (len(ordered) - index))
        shares[chat_id] = min(available[chat_id], fair)
        left -= shares[chat_id]
    return shares


def _policy_media(policy: str | None) -> tuple[str, ...] | None:
    if policy is None or policy == "all":
        return None
    if policy == "none":
        return ()
    return tuple(sorted(value.strip() for value in policy.split(",") if value.strip()))


def _later(first: str | None, second: str | None) -> str | None:
    values = [value for value in (first, second) if value]
    return max(values) if values else None


def _earlier(first: str | None, second: str | None) -> str | None:
    values = [value for value in (first, second) if value]
    return min(values) if values else None


def _after(value: str) -> str:
    moment = datetime.fromisoformat(value)
    return (moment + timedelta(microseconds=1)).isoformat()


def _period_label(scope: Scope) -> str:
    def fmt(value: str | None) -> str:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M") if value else "…"

    if not scope.since and not scope.until:
        return "all time"
    return f"{fmt(scope.since)} → {fmt(scope.until)}"


def _day(value: str | None) -> str:
    return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d") if value else "…"


def _best_effort(function: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    try:
        function(*args, **kwargs)
    except Exception:
        # Audit rows must never turn a read into a failure (e.g. a busy WAL writer).
        return
