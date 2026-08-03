"""
Base classes for TokenPak Vector DB Adapters.

Defines:
  - VectorBlock: portable block representation for retrieval results
  - VectorDBAdapter: abstract base class for all adapters
  - BatchQueryResult: result container for multi-query batches
"""

from __future__ import annotations

import hashlib
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


# ---------------------------------------------------------------------------
# Token estimation (no hard dep on tiktoken)
# ---------------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# VectorBlock — portable TokenPak block for vector DB results
# ---------------------------------------------------------------------------

@dataclass
class VectorBlock:
    """
    A TokenPak block produced from a vector DB retrieval result.

    Maps vector DB concepts to TokenPak block semantics:
      - id          → result ID from vector DB
      - content     → document text / payload text
      - block_type  → TokenPak block type (evidence, knowledge, etc.)
      - quality     → similarity/distance score mapped to 0-1
      - tokens      → estimated token count
      - metadata    → raw metadata from vector DB
      - provenance  → source attribution (db, collection, retrieved_at)
      - compressed  → whether content was truncated
    """
    id: str
    content: str
    block_type: str = "evidence"
    quality: float = 1.0
    tokens: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)
    compressed: bool = False
    _original_tokens: int = field(default=0, repr=False)

    def __post_init__(self):
        if not self.tokens:
            self.tokens = _estimate_tokens(self.content)
        if not self._original_tokens:
            self._original_tokens = self.tokens
        # Ensure quality is clamped 0-1
        self.quality = max(0.0, min(1.0, self.quality))

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to TokenPak wire format."""
        return {
            "id": self.id,
            "type": self.block_type,
            "content": self.content,
            "quality": self.quality,
            "tokens": self.tokens,
            "metadata": self.metadata,
            "provenance": self.provenance,
            "compressed": self.compressed,
        }

    def truncate(self, max_tokens: int) -> "VectorBlock":
        """Return a new VectorBlock with content truncated to max_tokens."""
        if self.tokens <= max_tokens:
            return self
        char_limit = max_tokens * 4
        truncated = self.content[:char_limit].rstrip()
        return VectorBlock(
            id=self.id,
            content=truncated,
            block_type=self.block_type,
            quality=self.quality,
            tokens=_estimate_tokens(truncated),
            metadata=self.metadata,
            provenance={**self.provenance, "truncated": True},
            compressed=True,
            _original_tokens=self._original_tokens or self.tokens,
        )

    def __repr__(self) -> str:
        preview = self.content[:60].replace("\n", " ")
        return (
            f"VectorBlock(id={self.id!r}, type={self.block_type!r}, "
            f"quality={self.quality:.3f}, tokens={self.tokens}, "
            f"content={preview!r}...)"
        )


# ---------------------------------------------------------------------------
# BatchQueryResult — result from multi-query batch
# ---------------------------------------------------------------------------

@dataclass
class BatchQueryResult:
    """Container for batch query results (one entry per query)."""
    queries: List[Any]
    results: List[List[VectorBlock]]
    elapsed_ms: float = 0.0

    @property
    def flat_blocks(self) -> List[VectorBlock]:
        """All blocks from all queries, flattened."""
        return [b for batch in self.results for b in batch]

    def __len__(self) -> int:
        return len(self.results)

    def __getitem__(self, idx: int) -> List[VectorBlock]:
        return self.results[idx]


# ---------------------------------------------------------------------------
# VectorDBAdapter — abstract base
# ---------------------------------------------------------------------------

class VectorDBAdapter(ABC):
    """
    Abstract base class for TokenPak vector DB adapters.

    Subclasses implement:
      - query_as_blocks(): single query → List[VectorBlock]
      - _result_to_block(): convert one raw result to VectorBlock

    Provides:
      - batch_query_as_blocks(): multi-query batch
      - _score_to_quality(): score normalization (override if needed)
    """

    #: Subclasses set this for provenance tracking
    source_type: str = "vectordb"

    def __init__(
        self,
        default_block_type: str = "evidence",
        default_limit: int = 10,
        content_field: str = "text",
    ):
        self.default_block_type = default_block_type
        self.default_limit = default_limit
        self.content_field = content_field

    @abstractmethod
    def query_as_blocks(
        self,
        query: Any,
        limit: int | None = None,
        block_type: str | None = None,
        **kwargs,
    ) -> List[VectorBlock]:
        """
        Query the vector DB and return results as VectorBlock list.

        Args:
            query: Query embedding (vector) or query string
            limit: Max results (defaults to self.default_limit)
            block_type: TokenPak block type (defaults to self.default_block_type)
            **kwargs: Adapter-specific parameters (namespace, collection, filters, etc.)

        Returns:
            List of VectorBlock ordered by descending quality
        """
        ...

    def batch_query_as_blocks(
        self,
        queries: Sequence[Any],
        limit: int | None = None,
        block_type: str | None = None,
        **kwargs,
    ) -> BatchQueryResult:
        """
        Run multiple queries, return BatchQueryResult.

        Default implementation loops over query_as_blocks().
        Subclasses may override for native batch API support.
        """
        t0 = time.monotonic()
        results = []
        for q in queries:
            try:
                blocks = self.query_as_blocks(q, limit=limit, block_type=block_type, **kwargs)
            except Exception:
                blocks = []
            results.append(blocks)
        elapsed_ms = (time.monotonic() - t0) * 1000
        return BatchQueryResult(queries=list(queries), results=results, elapsed_ms=elapsed_ms)

    def _score_to_quality(self, score: float, invert: bool = False) -> float:
        """
        Normalize a raw score to 0-1 quality.

        Args:
            score: Raw score from vector DB (e.g. cosine similarity or distance)
            invert: True if lower score = better (e.g. L2 distance)
        """
        if invert:
            # For distance metrics: quality = 1 / (1 + distance)
            return 1.0 / (1.0 + max(0.0, score))
        # For similarity metrics (already 0-1 or needs clamping)
        return max(0.0, min(1.0, float(score)))

    def _make_provenance(
        self,
        result_id: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build standard provenance dict."""
        prov = {
            "source_type": self.source_type,
            "source_id": result_id,
            "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if extra:
            prov.update(extra)
        return prov

    def _extract_content(self, payload: Dict[str, Any]) -> str:
        """Extract text content from a metadata/payload dict."""
        for field in (self.content_field, "text", "content", "body", "page_content", "chunk"):
            if field in payload and isinstance(payload[field], str):
                return payload[field]
        # Fallback: join all string values
        parts = [str(v) for v in payload.values() if isinstance(v, str)]
        return " ".join(parts) if parts else ""

    @abstractmethod
    def _result_to_block(
        self,
        result: Any,
        block_type: str,
    ) -> Optional[VectorBlock]:
        """Convert a single raw result to VectorBlock. Return None to skip."""
        ...
