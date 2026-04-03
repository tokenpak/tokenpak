"""
BM25 retriever — standalone implementation adapted from VaultIndex in proxy.py.
No external dependencies (stdlib only).
"""
from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import Retriever, RetrievalQuery, RetrievalResult, RetrieverType


# BM25 hyperparameters
_K1 = 1.5
_B = 0.75


@lru_cache(maxsize=512)
def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


class BM25Index:
    """In-memory BM25 index over a document corpus."""

    def __init__(self) -> None:
        self._docs: Dict[str, str] = {}          # doc_id -> content
        self._meta: Dict[str, Dict[str, Any]] = {}  # doc_id -> metadata
        self._df: Dict[str, int] = {}
        self._tfs: Dict[str, Dict[str, int]] = {}
        self._avg_dl: float = 0.0
        self._doc_count: int = 0

    def build(self, documents: List[Dict[str, Any]]) -> int:
        """Build index from document list. Each doc needs 'id' and 'content' keys."""
        self._docs = {}
        self._meta = {}
        df: Dict[str, int] = {}
        tfs: Dict[str, Dict[str, int]] = {}
        total_dl = 0

        for doc in documents:
            doc_id = str(doc["id"])
            content = str(doc.get("content", ""))
            self._docs[doc_id] = content
            self._meta[doc_id] = {k: v for k, v in doc.items() if k not in ("id", "content")}

            terms = _tokenize(content)
            tf: Dict[str, int] = {}
            for t in terms:
                tf[t] = tf.get(t, 0) + 1
            tfs[doc_id] = tf
            total_dl += len(terms)
            for t in set(terms):
                df[t] = df.get(t, 0) + 1

        doc_count = len(self._docs)
        self._df = df
        self._tfs = tfs
        self._avg_dl = total_dl / doc_count if doc_count > 0 else 0.0
        self._doc_count = doc_count
        return doc_count

    def search(self, query: str, top_k: int = 10, min_score: float = 0.0) -> List[RetrievalResult]:
        query_terms = _tokenize(query)
        if not query_terms or not self._docs:
            return []

        scores: Dict[str, float] = {}
        for doc_id in self._docs:
            tf = self._tfs.get(doc_id, {})
            dl = sum(tf.values())
            score = 0.0
            for qt in query_terms:
                if qt not in self._df:
                    continue
                n_qt = self._df[qt]
                idf = math.log((self._doc_count - n_qt + 0.5) / (n_qt + 0.5) + 1)
                term_freq = tf.get(qt, 0)
                if term_freq == 0:
                    continue
                numerator = term_freq * (_K1 + 1)
                denominator = term_freq + _K1 * (1 - _B + _B * dl / max(self._avg_dl, 1))
                score += idf * numerator / denominator
            if score > 0 and score >= min_score:
                scores[doc_id] = score

        ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:top_k]
        return [
            RetrievalResult(
                doc_id=doc_id,
                score=score,
                content=self._docs[doc_id],
                metadata=self._meta.get(doc_id, {}),
                retriever_type=RetrieverType.BM25,
            )
            for doc_id, score in ranked
        ]

    @property
    def doc_count(self) -> int:
        return self._doc_count


class BM25Retriever(Retriever):
    """
    BM25 retriever. Can be used standalone with in-memory documents,
    or backed by a vault index directory.
    """

    def __init__(self, vault_index_path: Optional[str] = None) -> None:
        self._vault_index_path = Path(vault_index_path) if vault_index_path else None
        self._index = BM25Index()
        self._loaded = False

    @property
    def retriever_type(self) -> RetrieverType:
        return RetrieverType.BM25

    def is_available(self) -> bool:
        return self._loaded or self._vault_index_path is not None

    def _load_vault(self) -> None:
        """Load documents from a vault index directory."""
        if self._vault_index_path is None:
            return
        import json

        index_file = self._vault_index_path / "index.json"
        blocks_dir = self._vault_index_path / "blocks"
        if not index_file.exists():
            return

        try:
            data = json.loads(index_file.read_text())
        except (json.JSONDecodeError, OSError):
            return

        raw_blocks = data.get("blocks", {})
        if not isinstance(raw_blocks, dict):
            return

        documents = []
        for bid, bdata in raw_blocks.items():
            content_file = blocks_dir / f"{bid}.txt"
            if not content_file.exists():
                continue
            try:
                content = content_file.read_text(errors="replace")
            except OSError:
                continue
            documents.append({
                "id": bid,
                "content": content,
                "source_path": bdata.get("source_path", bid),
                "risk_class": bdata.get("risk_class", "narrative"),
                "raw_tokens": bdata.get("raw_tokens", 0),
            })

        if documents:
            self._index.build(documents)
            self._loaded = True

    async def index(self, documents: List[Dict[str, Any]]) -> int:
        """Index a list of documents in-memory."""
        count = self._index.build(documents)
        self._loaded = count > 0
        return count

    async def search(self, query: RetrievalQuery) -> List[RetrievalResult]:
        if not self._loaded:
            if self._vault_index_path:
                self._load_vault()
            if not self._loaded:
                return []

        return self._index.search(
            query.text,
            top_k=query.top_k,
            min_score=query.min_score,
        )
