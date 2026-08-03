from __future__ import annotations

import json
import sys
from dataclasses import asdict
from typing import Any

from . import __version__
from .assistant import ArchiveAssistant, expand_cited_sources, knowledge_catalog_lookup
from .config import AppConfig, load_config
from .hybrid_retrieval import RetrievalMode, SemanticUnavailableError
from .knowledge_catalog import KnowledgeScope
from .models import SearchFilters
from .security import (
    AgentOperation,
    AgentPolicyError,
    RequestedAgentScope,
    audit_policy_decision,
    require_agent_policy,
)
from .storage import Database


class ReadOnlyMCPServer:
    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        method = request.get("method")
        request_id = request.get("id")
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-03-26",
                    "serverInfo": {"name": "tg-recall", "version": __version__},
                    "capabilities": {"tools": {}},
                }
            elif method == "tools/list":
                result = {"tools": self.tools()}
            elif method == "tools/call":
                params = request.get("params") or {}
                result = self.call_tool(params.get("name"), params.get("arguments") or {})
            else:
                return self.error(request_id, -32601, f"Unknown method: {method}")
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except AgentPolicyError as exc:
            return self.error(request_id, -32000, str(exc), details={"code": exc.error_code, **exc.details})
        except SemanticUnavailableError as exc:
            return self.error(request_id, -32000, str(exc), details={"code": exc.code, "reason": exc.reason})
        except Exception as exc:
            return self.error(request_id, -32000, str(exc))

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "list_allowed_chats",
                "description": "List cached chats allowed by AI access policy.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "list_scopes",
                "description": "List saved sync scopes.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "search_messages",
                "description": "Search indexed messages and transcripts with citations.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "chat_id": {"type": "integer"},
                        "limit": {"type": "integer"},
                        "since": {"type": "string"},
                        "until": {"type": "string"},
                        "media_type": {"type": "string"},
                    },
                    "required": ["query", "chat_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_message_context",
                "description": "Return nearby cached messages around a cited message.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "chat_id": {"type": "integer"},
                        "message_id": {"type": "integer"},
                        "radius": {"type": "integer"},
                        "since": {"type": "string"},
                        "until": {"type": "string"},
                    },
                    "required": ["chat_id", "message_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "ask_archive",
                "description": "Answer using bounded local archive retrieval.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "chat_id": {"type": "integer"},
                        "limit": {"type": "integer"},
                        "since": {"type": "string"},
                        "until": {"type": "string"},
                        "media_type": {"type": "string"},
                    },
                    "required": ["query", "chat_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "retrieve_evidence",
                "description": "Return bounded cited local evidence with optional genuine local-vector retrieval. Read-only; never builds or changes an index.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "chat_id": {"type": "integer"},
                        "limit": {"type": "integer"},
                        "since": {"type": "string"},
                        "until": {"type": "string"},
                        "media_type": {"type": "string"},
                        "mode": {"type": "string", "enum": [mode.value for mode in RetrievalMode]},
                        "token_budget": {"type": "integer", "minimum": 1, "maximum": 20000},
                        "context": {"type": "integer", "minimum": 0, "maximum": 8},
                    },
                    "required": ["query", "chat_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "query_knowledge_catalog",
                "description": "Read compact cited catalog metadata in one exact saved scope; no raw source body or writes.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"}, "chat_id": {"type": "integer"},
                        "scope_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    "required": ["query", "chat_id", "scope_id"], "additionalProperties": False,
                },
            },
            {
                "name": "inspect_research_session",
                "description": "Read compact checkpoint/session metadata for an explicitly allowed session; no mutation.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"session_id": {"type": "string"}, "chat_id": {"type": "integer"}},
                    "required": ["session_id", "chat_id"], "additionalProperties": False,
                },
            },
            {
                "name": "expand_cited_sources",
                "description": "Expand only explicit cited Telegram sources under current scope and bounded payload/work budgets.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"}, "chat_id": {"type": "integer"},
                        "citations": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 8},
                        "context": {"type": "integer", "minimum": 0, "maximum": 8},
                        "token_budget": {"type": "integer", "minimum": 1, "maximum": 20000},
                    },
                    "required": ["session_id", "chat_id", "citations"], "additionalProperties": False,
                },
            },
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "list_allowed_chats":
            self._enforce(AgentOperation.METADATA_LIST, arguments)
            allowed = set(self.config.ai_access.allowed_chat_ids)
            chats = [dict(row) for row in self.db.list_chats() if row["chat_id"] in allowed]
            self._audit(name, arguments, len(chats))
            return {"content": [{"type": "text", "text": json.dumps(chats, ensure_ascii=False)}]}
        if name == "list_scopes":
            self._enforce(AgentOperation.METADATA_LIST, arguments)
            scopes = []
            for scope in self.db.list_scopes():
                try:
                    self._enforce_complete_saved_scope(scope)
                except AgentPolicyError:
                    continue
                scopes.append(scope)
            self._audit(name, arguments, len(scopes))
            return {"content": [{"type": "text", "text": json.dumps(scopes, ensure_ascii=False)}]}
        if name == "search_messages":
            decision = self._enforce(AgentOperation.ARCHIVE_READ, arguments)
            results = self.db.search(
                arguments["query"],
                limit=decision.result_limit or 1,
                filters=_decision_filters(decision),
            )
            self._audit(name, arguments, len(results))
            return {"content": [{"type": "text", "text": json.dumps([_result_dict(item) for item in results], ensure_ascii=False)}]}
        if name == "get_message_context":
            decision = self._enforce(AgentOperation.ARCHIVE_READ, arguments)
            items = self.db.message_context(
                decision.chat_ids[0],
                arguments["message_id"],
                radius=arguments.get("radius", 3),
                filters=_decision_filters(decision),
            )[: decision.result_limit or 1]
            self._audit(name, arguments, len(items))
            return {"content": [{"type": "text", "text": json.dumps([_result_dict(item) for item in items], ensure_ascii=False)}]}
        if name == "ask_archive":
            decision = self._enforce(AgentOperation.ARCHIVE_READ, arguments)
            answer = ArchiveAssistant(self.db, self.config).extractive_answer(
                arguments["query"],
                limit=decision.result_limit or 1,
                chat_id=decision.chat_ids[0],
                filters=_decision_filters(decision),
            )
            self._audit(name, arguments, 1)
            return {"content": [{"type": "text", "text": answer}]}
        if name == "retrieve_evidence":
            decision = self._enforce(AgentOperation.ARCHIVE_READ, arguments)
            token_budget = int(arguments.get("token_budget", 8000))
            context = int(arguments.get("context", 3))
            if not 1 <= token_budget <= 20000:
                raise ValueError("token_budget must be between 1 and 20000")
            if not 0 <= context <= 8:
                raise ValueError("context must be between 0 and 8")
            result = ArchiveAssistant(self.db, self.config).retrieve_hybrid(
                arguments["query"],
                filters=_decision_filters(decision),
                limit=decision.result_limit or 1,
                token_budget=token_budget,
                context_radius=context,
                mode=RetrievalMode(arguments.get("mode", "auto")),
            )
            payload = result.as_json()
            self._audit(name, arguments, len(payload["evidence"]))
            return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
        if name == "query_knowledge_catalog":
            decision = self._enforce(AgentOperation.ARCHIVE_READ, arguments)
            saved = self.db.get_scope(arguments["scope_id"])
            if saved is None or int(arguments["chat_id"]) not in saved["chat_ids"]:
                raise AgentPolicyError(_knowledge_scope_denial(decision))
            decision = self._enforce_complete_saved_scope(saved, result_limit=arguments.get("limit"))
            payload = knowledge_catalog_lookup(
                self.db, profile_id=self.config.profile, scope_id=arguments["scope_id"],
                chat_ids=tuple(decision.chat_ids), query=arguments["query"], limit=decision.result_limit or 1,
                filters=_decision_filters(decision),
            )
            self._audit(name, arguments, len(payload["hits"]))
            return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
        if name == "inspect_research_session":
            decision = self._enforce(AgentOperation.ARCHIVE_READ, arguments)
            session = self.db.research_session_view(profile_id=self.config.profile, session_id=arguments["session_id"])
            if int(arguments["chat_id"]) not in session["scope"]["chat_ids"]:
                raise AgentPolicyError(_knowledge_scope_denial(decision))
            saved = self.db.get_scope(session["scope"]["scope_id"])
            if saved is None or tuple(sorted(saved["chat_ids"])) != tuple(sorted(session["scope"]["chat_ids"])):
                raise AgentPolicyError(_knowledge_scope_denial(decision))
            self._enforce_complete_saved_scope(saved)
            self._audit(name, arguments, 1)
            return {"content": [{"type": "text", "text": json.dumps(session, ensure_ascii=False)}]}
        if name == "expand_cited_sources":
            decision = self._enforce(AgentOperation.ARCHIVE_READ, arguments)
            session = self.db.research_session_view(profile_id=self.config.profile, session_id=arguments["session_id"])
            session_chats = set(session["scope"]["chat_ids"])
            if not set(decision.chat_ids) <= session_chats:
                raise AgentPolicyError(_knowledge_scope_denial(decision))
            token_budget = int(arguments.get("token_budget", 4000))
            context = int(arguments.get("context", 2))
            if not 1 <= token_budget <= 20000 or not 0 <= context <= 8:
                raise ValueError("invalid bounded expansion budget")
            payload = expand_cited_sources(
                self.db,
                scope=KnowledgeScope(self.config.profile, tuple(decision.chat_ids)),
                citations=tuple(arguments["citations"]),
                filters=_decision_filters(decision),
                item_limit=min(int(arguments.get("limit", 8)), decision.result_limit or 1),
                context_radius=context,
                token_budget=token_budget,
            )
            self._audit(name, arguments, len(payload["items"]))
            return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
        raise PermissionError(f"MCP tool is not available or not read-only: {name}")

    def _enforce(self, operation: AgentOperation, arguments: dict[str, Any]):
        requested = RequestedAgentScope(
            chat_ids=(int(arguments["chat_id"]),) if arguments.get("chat_id") is not None else (),
            since=arguments.get("since"),
            until=arguments.get("until"),
            media_policy=arguments.get("media_type"),
            result_limit=arguments.get("limit"),
        )
        return self._enforce_requested(operation, requested)

    def _enforce_complete_saved_scope(self, saved: dict[str, Any], *, result_limit: int | None = None):
        """Allow resumable metadata only when AI policy covers the saved scope exactly."""

        requested = RequestedAgentScope(
            chat_ids=tuple(saved["chat_ids"]),
            since=saved.get("since"),
            until=saved.get("until"),
            media_policy=saved.get("media_policy"),
            result_limit=result_limit,
            saved_scope=saved,
        )
        decision = self._enforce_requested(AgentOperation.ARCHIVE_READ, requested)
        if (
            tuple(sorted(decision.chat_ids)) != tuple(sorted(saved["chat_ids"]))
            or decision.since != saved.get("since")
            or decision.until != saved.get("until")
            or decision.media_policy != saved.get("media_policy")
        ):
            raise AgentPolicyError(_knowledge_scope_denial(decision))
        return decision

    def _enforce_requested(self, operation: AgentOperation, requested: RequestedAgentScope):
        policy = self.config.ai_access
        try:
            decision = require_agent_policy(
                operation,
                enabled=policy.enabled,
                allowed_chat_ids=policy.allowed_chat_ids,
                max_results=policy.max_results,
                allowed_since=policy.allowed_since,
                allowed_until=policy.allowed_until,
                allowed_media_types=policy.allowed_media_types,
                requested=requested,
                automation=True,
            )
        except AgentPolicyError as exc:
            audit_policy_decision(self.db, exc.decision, requested=requested)
            raise
        audit_policy_decision(self.db, decision, requested=requested)
        return decision

    def _audit(self, tool_name: str, arguments: dict[str, Any], result_count: int) -> None:
        self.db.audit(
            "mcp_tool_call",
            tool_name,
            chat_id=arguments.get("chat_id"),
            result_count=result_count,
        )

    @staticmethod
    def error(request_id: Any, code: int, message: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if details:
            error["data"] = details
        return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _result_dict(item: Any) -> dict[str, Any]:
    data = asdict(item)
    data["citation"] = item.citation
    return data


def _media_filter_types(policy: str | None) -> tuple[str, ...] | None:
    if policy is None or policy == "all":
        return None
    if policy == "none":
        return ()
    return tuple(sorted(value.strip() for value in policy.split(",") if value.strip()))


def _decision_filters(decision: Any) -> SearchFilters:
    return SearchFilters(
        chat_id=decision.chat_ids[0],
        since=decision.since,
        until=decision.until,
        media_types=_media_filter_types(decision.media_policy),
    )


def _knowledge_scope_denial(decision: Any) -> Any:
    from .security import PolicyDecision

    return PolicyDecision(
        operation=decision.operation,
        allowed=False,
        error_code="knowledge_scope_narrowed",
        message="catalog and session metadata require the complete exact saved scope",
    )


def main() -> int:
    config = load_config()
    config.ensure_dirs()
    db = Database(config.db_path)
    db.migrate()
    server = ReadOnlyMCPServer(config, db)
    for line in sys.stdin:
        if not line.strip():
            continue
        response = server.handle(json.loads(line))
        print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


def legacy_main() -> int:
    print("warning: tg-ecosystem-mcp is deprecated; use tg-recall-mcp", file=sys.stderr)
    return main()


if __name__ == "__main__":
    raise SystemExit(main())
