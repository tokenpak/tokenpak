# SPDX-License-Identifier: Apache-2.0
"""Chunk schema for TokenPak retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class ChunkSchema:
    """Chunk schema: searchable retrieval unit."""

    id: str
    """Unique chunk identifier."""

    source: str
    """Source artifact or file path."""

    content: str
    """Chunk content (200-500 tokens)."""

    token_estimate: int
    """Estimated tokens."""

    symbols: List[str] = field(default_factory=list)
    """Extracted symbols (function names, class names, etc.)."""

    embedding_ref: Optional[str] = None
    """Reference to stored embedding (for semantic search)."""

    neighbors: List[str] = field(default_factory=list)
    """IDs of neighboring chunks (for context expansion)."""

    metadata: Dict[str, Any] = field(default_factory=dict)
    """Chunk metadata (language, line_range, etc.)."""

    created_at: datetime = field(default_factory=datetime.utcnow)
    """When chunk was created."""

    stats: Dict[str, Any] = field(default_factory=dict)
    """Retrieval stats (frequency, rank, etc.)."""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for serialization."""
        return {
            "id": self.id,
            "source": self.source,
            "content": self.content,
            "token_estimate": self.token_estimate,
            "symbols": self.symbols,
            "embedding_ref": self.embedding_ref,
            "neighbors": self.neighbors,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "stats": self.stats,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ChunkSchema:
        """Create from dict."""
        data = dict(data)  # Copy to avoid mutation
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        return cls(**data)
