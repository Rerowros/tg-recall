from __future__ import annotations

import json
import sys
from dataclasses import asdict
from typing import Any

from .assistant import ArchiveAssistant
from .config import AppConfig, load_config
from .models import SearchFilters
from .security import enforce_ai_archive_read
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
                    "serverInfo": {"name": "tg-recall", "version": "0.2.0"},
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
                    },
                    "required": ["query", "chat_id"],
                    "additionalProperties": False,
                },
            },
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "list_allowed_chats":
            self._audit(name, arguments, 0)
            allowed = set(self.config.ai_access.allowed_chat_ids)
            chats = [dict(row) for row in self.db.list_chats() if row["chat_id"] in allowed]
            self._audit(name, arguments, len(chats))
            return {"content": [{"type": "text", "text": json.dumps(chats, ensure_ascii=False)}]}
        if name == "list_scopes":
            scopes = self.db.list_scopes()
            self._audit(name, arguments, len(scopes))
            return {"content": [{"type": "text", "text": json.dumps(scopes, ensure_ascii=False)}]}
        if name == "search_messages":
            limit = self._enforce(arguments)
            results = self.db.search(
                arguments["query"],
                limit=limit,
                filters=SearchFilters(chat_id=arguments["chat_id"]),
            )
            self._audit(name, arguments, len(results))
            return {"content": [{"type": "text", "text": json.dumps([_result_dict(item) for item in results], ensure_ascii=False)}]}
        if name == "get_message_context":
            self._enforce(arguments)
            items = self.db.message_context(arguments["chat_id"], arguments["message_id"], radius=arguments.get("radius", 3))
            self._audit(name, arguments, len(items))
            return {"content": [{"type": "text", "text": json.dumps([_result_dict(item) for item in items], ensure_ascii=False)}]}
        if name == "ask_archive":
            limit = self._enforce(arguments)
            answer = ArchiveAssistant(self.db, self.config).answer(arguments["query"], limit=limit, chat_id=arguments["chat_id"])
            self._audit(name, arguments, 1)
            return {"content": [{"type": "text", "text": answer}]}
        raise PermissionError(f"MCP tool is not available or not read-only: {name}")

    def _enforce(self, arguments: dict[str, Any]) -> int:
        chat_id = arguments.get("chat_id")
        if not self.config.ai_access.enabled:
            raise PermissionError("MCP archive access is disabled")
        if chat_id is None:
            raise PermissionError("MCP archive access requires chat_id")
        if chat_id not in self.config.ai_access.allowed_chat_ids:
            raise PermissionError(f"chat {chat_id} is not allowed for MCP archive access")
        return min(arguments.get("limit", self.config.ai_access.max_results), self.config.ai_access.max_results)

    def _audit(self, tool_name: str, arguments: dict[str, Any], result_count: int) -> None:
        self.db.audit(
            "mcp_tool_call",
            tool_name,
            chat_id=arguments.get("chat_id"),
            result_count=result_count,
        )

    @staticmethod
    def error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result_dict(item: Any) -> dict[str, Any]:
    data = asdict(item)
    data["citation"] = item.citation
    return data


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
