from __future__ import annotations

from dataclasses import dataclass

from .config import AppConfig
from .models import SearchResult
from .storage import Database


@dataclass(frozen=True)
class EvidenceContext:
    query: str
    items: list[SearchResult]

    def as_prompt_context(self) -> str:
        lines = [f"Query: {self.query}", "Evidence:"]
        for index, item in enumerate(self.items, start=1):
            lines.append(
                f"[{index}] {item.timestamp} {item.chat_title} "
                f"{item.citation}: {item.text}"
            )
        return "\n".join(lines)


class ArchiveAssistant:
    def __init__(self, db: Database, config: AppConfig | None = None):
        self.db = db
        self.config = config

    def retrieve(self, query: str, limit: int = 10, chat_id: int | None = None) -> EvidenceContext:
        return EvidenceContext(query=query, items=self.db.search(query=query, limit=limit, chat_id=chat_id))

    def answer(self, query: str, limit: int = 10, chat_id: int | None = None) -> str:
        context = self.retrieve(query, limit=limit, chat_id=chat_id)
        provider = self.config.llm.provider if self.config else "extractive"
        if provider != "extractive":
            if not self.config or not self.config.provider_policy.external_llm_enabled:
                raise PermissionError("external LLM provider is disabled by provider policy")
            raise NotImplementedError(f"LLM provider is configured but not implemented: {provider}")
        if not context.items:
            return "No matching archive evidence found."
        lines = ["Local extractive answer from retrieved evidence:"]
        for item in context.items:
            lines.append(f"- {item.citation}: {item.text}")
        return "\n".join(lines)
