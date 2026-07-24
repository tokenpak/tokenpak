"""
Base abstractions for the hybrid retrieval system.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class RetrieverType(Enum):
    BM25 = "bm25"
    VECTOR = "vector"
    HYBRID = "hybrid"


@dataclass
class RetrievalResult:
    """A single result from any retriever."""

    doc_id: str
    score: float
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    retriever_type: RetrieverType = RetrieverType.BM25

    def __repr__(self) -> str:
        return f"RetrievalResult(doc_id={self.doc_id!r}, score={self.score:.4f})"


@dataclass
class RetrievalQuery:
    """Query parameters for retrieval."""

    text: str
    top_k: int = 10
    min_score: float = 0.0
    filters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FusedResult:
    """Result after RRF fusion across multiple retrievers."""

    doc_id: str
    fused_score: float
    source_results: Dict[str, RetrievalResult] = field(default_factory=dict)

    @property
    def content(self) -> str:
        for r in self.source_results.values():
            if r.content:
                return r.content
        return ""

    @property
    def metadata(self) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}
        for r in self.source_results.values():
            merged.update(r.metadata)
        return merged

    def __repr__(self) -> str:
        return f"FusedResult(doc_id={self.doc_id!r}, fused_score={self.fused_score:.6f})"


@dataclass
class HybridSearchConfig:
    """Configuration for the hybrid retriever."""

    # BM25 settings
    bm25_weight: float = 0.5
    bm25_min_score: float = 0.0
    # Vector settings
    vector_weight: float = 0.5
    vector_model: str = "all-MiniLM-L6-v2"
    vector_index_path: Optional[str] = None
    # Fusion settings
    rrf_k: int = 60
    top_k: int = 20
    # Vault index path for BM25
    vault_index_path: Optional[str] = None

    @classmethod
    def from_env(cls) -> "HybridSearchConfig":
        """Load configuration from environment variables.

        Supported env vars:
            TOKENPAK_RETRIEVAL_MODE     bm25|vector|hybrid (affects weights)
            TOKENPAK_BM25_WEIGHT        float (default 0.5)
            TOKENPAK_BM25_MIN_SCORE     float (default 0.0)
            TOKENPAK_VECTOR_WEIGHT      float (default 0.5)
            TOKENPAK_VECTOR_MODEL       str (default all-MiniLM-L6-v2)
            TOKENPAK_VECTOR_INDEX_PATH  path to .npz index file
            TOKENPAK_RRF_K              int (default 60)
            TOKENPAK_RETRIEVAL_TOP_K    int (default 20)
            TOKENPAK_VAULT_INDEX_PATH   path to vault .tokenpak directory
        """

        def _float(key: str, default: float) -> float:
            try:
                return float(os.environ[key])
            except (KeyError, ValueError):
                return default

        def _int(key: str, default: int) -> int:
            try:
                return int(os.environ[key])
            except (KeyError, ValueError):
                return default

        # Determine weights based on TOKENPAK_RETRIEVAL_MODE shorthand
        mode = os.environ.get("TOKENPAK_RETRIEVAL_MODE", "").lower()
        if mode == "bm25":
            default_bm25_w, default_vec_w = 1.0, 0.0
        elif mode == "vector":
            default_bm25_w, default_vec_w = 0.0, 1.0
        else:
            default_bm25_w, default_vec_w = 0.5, 0.5

        return cls(
            bm25_weight=_float("TOKENPAK_BM25_WEIGHT", default_bm25_w),
            bm25_min_score=_float("TOKENPAK_BM25_MIN_SCORE", 0.0),
            vector_weight=_float("TOKENPAK_VECTOR_WEIGHT", default_vec_w),
            vector_model=os.environ.get("TOKENPAK_VECTOR_MODEL", "all-MiniLM-L6-v2"),
            vector_index_path=os.environ.get("TOKENPAK_VECTOR_INDEX_PATH"),
            rrf_k=_int("TOKENPAK_RRF_K", 60),
            top_k=_int("TOKENPAK_RETRIEVAL_TOP_K", 20),
            vault_index_path=os.environ.get("TOKENPAK_VAULT_INDEX_PATH"),
        )


class Retriever(ABC):
    """Abstract base class for all retrievers."""

    @property
    @abstractmethod
    def retriever_type(self) -> RetrieverType: ...

    @abstractmethod
    async def search(self, query: RetrievalQuery) -> List[RetrievalResult]:
        """Search and return ranked results."""
        ...

    @abstractmethod
    async def index(self, documents: List[Dict[str, Any]]) -> int:
        """Index documents. Returns count of indexed documents."""
        ...

    def is_available(self) -> bool:
        """Whether this retriever is ready to serve queries."""
        return True
