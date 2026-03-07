"""TokenPakMemory for Semantic Kernel-oriented retrieval and prompt export."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class _MemoryEntry:
    collection: str
    id: str
    text: str
    metadata: Dict[str, Any]
    created_at: datetime


class TokenPakMemory:
    """In-memory semantic collection with basic relevance scoring and eviction."""

    def __init__(self, budget: int = 4000, compaction_mode: str = "balanced", max_entries: int = 100):
        self.budget = budget
        self.compaction_mode = compaction_mode
        self.max_entries = max_entries
        self._entries: List[_MemoryEntry] = []
        self._by_collection: Dict[str, List[_MemoryEntry]] = defaultdict(list)

    async def save_information(
        self,
        collection: str,
        text: str,
        id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Store or replace one information item in a collection."""
        existing = self._find_entry(collection=collection, entry_id=id)
        if existing is not None:
            self._remove_entry(existing)

        entry = _MemoryEntry(
            collection=collection,
            id=id,
            text=text,
            metadata=metadata or {},
            created_at=datetime.now(timezone.utc),
        )

        self._entries.append(entry)
        self._by_collection[collection].append(entry)
        self._enforce_capacity()

    async def get_information(
        self,
        collection: str,
        query: str,
        limit: int = 5,
        min_relevance_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Retrieve relevant entries for a collection ordered by score."""
        candidates = self._by_collection.get(collection, [])
        scored: List[Dict[str, Any]] = []
        for entry in candidates:
            score = self._score(query, entry.text)
            if score >= min_relevance_score:
                scored.append(
                    {
                        "id": entry.id,
                        "text": entry.text,
                        "metadata": dict(entry.metadata),
                        "collection": entry.collection,
                        "relevance_score": score,
                        "created_at": entry.created_at,
                    }
                )

        scored.sort(key=lambda item: item["relevance_score"], reverse=True)
        return scored[: max(0, limit)]

    def to_prompt(self, collection: Optional[str] = None) -> str:
        """Export memory as budget-capped plain text for prompting."""
        if collection is None:
            source = list(self._entries)
        else:
            source = list(self._by_collection.get(collection, []))

        lines: List[str] = []
        budget_chars = self.budget * 4
        running = 0

        for entry in source:
            line = f"[{entry.collection}/{entry.id}] {entry.text}"
            if running + len(line) + 1 > budget_chars:
                remaining = budget_chars - running
                if remaining > 3:
                    lines.append(line[: remaining - 3] + "...")
                break
            lines.append(line)
            running += len(line) + 1

        return "\n".join(lines)

    def clear(self, collection: Optional[str] = None) -> None:
        """Clear all entries or one collection."""
        if collection is None:
            self._entries.clear()
            self._by_collection.clear()
            return

        for entry in list(self._by_collection.get(collection, [])):
            self._remove_entry(entry)

    @property
    def entry_count(self) -> int:
        """Return current entry count across all collections."""
        return len(self._entries)

    def collections(self) -> List[str]:
        """Return sorted names of non-empty collections."""
        return sorted([name for name, values in self._by_collection.items() if values])

    def _find_entry(self, collection: str, entry_id: str) -> Optional[_MemoryEntry]:
        for entry in self._by_collection.get(collection, []):
            if entry.id == entry_id:
                return entry
        return None

    def _remove_entry(self, entry: _MemoryEntry) -> None:
        if entry in self._entries:
            self._entries.remove(entry)
        collection_entries = self._by_collection.get(entry.collection, [])
        if entry in collection_entries:
            collection_entries.remove(entry)
        if not collection_entries and entry.collection in self._by_collection:
            del self._by_collection[entry.collection]

    def _enforce_capacity(self) -> None:
        while len(self._entries) > self.max_entries:
            oldest = self._entries[0]
            self._remove_entry(oldest)

    def _score(self, query: str, text: str) -> float:
        query_terms = self._terms(query)
        text_terms = self._terms(text)
        if not query_terms:
            return 1.0 if text else 0.0
        overlap = len(query_terms.intersection(text_terms))
        return overlap / len(query_terms)

    def _terms(self, value: str) -> set[str]:
        return {piece.lower() for piece in value.split() if piece.strip()}
