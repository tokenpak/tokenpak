"""Adapter registry with priority-based format detection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Mapping, Optional

from .base import FormatAdapter


@dataclass
class _RegisteredAdapter:
    adapter: FormatAdapter
    priority: int


class AdapterRegistry:
    """Registry for provider format adapters."""

    def __init__(self) -> None:
        self._items: List[_RegisteredAdapter] = []

    def register(self, adapter: FormatAdapter, priority: int = 100) -> None:
        self._items.append(_RegisteredAdapter(adapter=adapter, priority=priority))
        self._items.sort(key=lambda item: item.priority, reverse=True)

    def detect(
        self,
        path: str,
        headers: Mapping[str, str],
        body: Optional[bytes] = None,
    ) -> FormatAdapter:
        for item in self._items:
            if item.adapter.detect(path, headers, body):
                return item.adapter
        raise RuntimeError("No adapter matched request; ensure passthrough adapter is registered")

    def list_formats(self) -> List[str]:
        return [item.adapter.source_format for item in self._items]

    def adapters(self) -> List[FormatAdapter]:
        return [item.adapter for item in self._items]
