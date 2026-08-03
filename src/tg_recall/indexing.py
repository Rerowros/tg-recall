from __future__ import annotations

from .models import SearchResult
from .storage import Database


class ArchiveIndex:
    def __init__(self, db: Database):
        self.db = db

    def search(self, query: str, limit: int = 20, chat_id: int | None = None) -> list[SearchResult]:
        return self.db.search(query=query, limit=limit, chat_id=chat_id)
