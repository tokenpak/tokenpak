"""
Local vector retriever using sentence-transformers + numpy (faiss optional).
Gracefully degrades if sentence-transformers is not installed.
"""
from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import RetrievalQuery, RetrievalResult, Retriever, RetrieverType

logger = logging.getLogger(__name__)

# Optional dependency checks
try:
    from sentence_transformers import SentenceTransformer
    _ST_AVAILABLE = True
except ImportError:
    _ST_AVAILABLE = False
    SentenceTransformer = None  # type: ignore[misc,assignment]

try:
    import numpy as np
    _NP_AVAILABLE = True
except ImportError:
    _NP_AVAILABLE = False
    np = None  # type: ignore[assignment]

try:
    import faiss
    _FAISS_AVAILABLE = True
except ImportError:
    _FAISS_AVAILABLE = False
    faiss = None  # type: ignore[assignment]


def _cosine_similarity_numpy(query_vec: "np.ndarray", matrix: "np.ndarray") -> "np.ndarray":
    """Compute cosine similarity between a query vector and a matrix of document vectors."""
    query_norm = np.linalg.norm(query_vec)
    if query_norm == 0:
        return np.zeros(len(matrix))
    q = query_vec / query_norm
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    normalized = matrix / norms
    return normalized @ q


class LocalVectorRetriever(Retriever):
    """
    Vector similarity retriever backed by sentence-transformers embeddings.
    Falls back gracefully if sentence-transformers or numpy is unavailable.

    Storage layout (when index_path is set):
        <index_path>/embeddings.npy   — float32 numpy array (N, dim)
        <index_path>/doc_ids.txt      — one doc_id per line
        <index_path>/contents.txt     — one content per line (newline-escaped)
        <index_path>/meta.json        — list of metadata dicts
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        index_path: Optional[str] = None,
    ) -> None:
        self._model_name = model_name
        self._index_path = Path(index_path) if index_path else None
        self._model: Optional[Any] = None
        self._embeddings: Optional[Any] = None  # np.ndarray (N, dim)
        self._doc_ids: List[str] = []
        self._contents: List[str] = []
        self._meta: List[Dict[str, Any]] = []
        self._faiss_index: Optional[Any] = None
        self._available: bool = _ST_AVAILABLE and _NP_AVAILABLE
        self._loaded: bool = False

        if not _ST_AVAILABLE:
            warnings.warn(
                "sentence-transformers not installed. LocalVectorRetriever will return empty results. "
                "Install with: pip install sentence-transformers",
                ImportWarning,
                stacklevel=2,
            )
        if not _NP_AVAILABLE:
            warnings.warn(
                "numpy not installed. LocalVectorRetriever will return empty results.",
                ImportWarning,
                stacklevel=2,
            )

    @property
    def retriever_type(self) -> RetrieverType:
        return RetrieverType.VECTOR

    def is_available(self) -> bool:
        return self._available

    def _ensure_model(self) -> bool:
        """Lazy-load the sentence-transformers model."""
        if not self._available:
            return False
        if self._model is None:
            try:
                self._model = SentenceTransformer(self._model_name)
            except Exception as e:
                logger.warning("Failed to load sentence-transformers model %r: %s", self._model_name, e)
                self._available = False
                return False
        return True

    def _build_faiss(self) -> None:
        """Build a faiss index from current embeddings (optional acceleration)."""
        if not _FAISS_AVAILABLE or self._embeddings is None:
            return
        try:
            dim = self._embeddings.shape[1]
            index = faiss.IndexFlatIP(dim)  # inner product (works with normalized vectors)
            # Normalize embeddings for cosine similarity via inner product
            norms = np.linalg.norm(self._embeddings, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1.0, norms)
            normalized = (self._embeddings / norms).astype("float32")
            index.add(normalized)
            self._faiss_index = index
        except Exception as e:
            logger.debug("faiss index build failed (non-fatal): %s", e)
            self._faiss_index = None

    async def index(self, documents: List[Dict[str, Any]]) -> int:
        """Embed and index documents. Each doc needs 'id' and 'content' keys."""
        if not self._ensure_model():
            return 0

        texts = [str(doc.get("content", "")) for doc in documents]
        doc_ids = [str(doc["id"]) for doc in documents]
        meta = [{k: v for k, v in doc.items() if k not in ("id", "content")} for doc in documents]

        try:
            embeddings = self._model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
        except Exception as e:
            logger.error("Embedding failed: %s", e)
            return 0

        self._embeddings = np.array(embeddings, dtype="float32")
        self._doc_ids = doc_ids
        self._contents = texts
        self._meta = meta
        self._loaded = True

        if _FAISS_AVAILABLE:
            self._build_faiss()

        if self._index_path:
            self.save()

        return len(documents)

    async def search(self, query: RetrievalQuery) -> List[RetrievalResult]:
        if not self._available:
            logger.debug("LocalVectorRetriever unavailable, returning empty results")
            return []

        if not self._loaded:
            if self._index_path:
                self.load()
            if not self._loaded:
                return []

        if not self._ensure_model():
            return []

        try:
            q_vec = self._model.encode([query.text], show_progress_bar=False, convert_to_numpy=True)[0]
            q_vec = np.array(q_vec, dtype="float32")
        except Exception as e:
            logger.error("Query embedding failed: %s", e)
            return []

        top_k = query.top_k

        if self._faiss_index is not None:
            try:
                q_norm = np.linalg.norm(q_vec)
                q_normalized = (q_vec / q_norm if q_norm > 0 else q_vec).reshape(1, -1).astype("float32")
                scores_arr, indices = self._faiss_index.search(q_normalized, min(top_k, len(self._doc_ids)))
                pairs: List[Tuple[int, float]] = [
                    (int(idx), float(sc))
                    for idx, sc in zip(indices[0], scores_arr[0])
                    if idx >= 0
                ]
            except Exception:
                pairs = self._numpy_search(q_vec, top_k)
        else:
            pairs = self._numpy_search(q_vec, top_k)

        results = []
        for idx, score in pairs:
            if score < query.min_score:
                continue
            results.append(RetrievalResult(
                doc_id=self._doc_ids[idx],
                score=score,
                content=self._contents[idx],
                metadata=self._meta[idx] if idx < len(self._meta) else {},
                retriever_type=RetrieverType.VECTOR,
            ))
        return results

    def _numpy_search(self, q_vec: "np.ndarray", top_k: int) -> List[Tuple[int, float]]:
        if self._embeddings is None or len(self._embeddings) == 0:
            return []
        sims = _cosine_similarity_numpy(q_vec, self._embeddings)
        top_indices = np.argsort(-sims)[:top_k]
        return [(int(i), float(sims[i])) for i in top_indices]

    def save(self) -> None:
        """Persist embeddings to disk."""
        if self._index_path is None or self._embeddings is None:
            return
        import json

        self._index_path.mkdir(parents=True, exist_ok=True)
        np.save(str(self._index_path / "embeddings.npy"), self._embeddings)
        (self._index_path / "doc_ids.txt").write_text(
            "\n".join(self._doc_ids), encoding="utf-8"
        )
        # Escape newlines in content for single-line storage
        escaped = [c.replace("\\", "\\\\").replace("\n", "\\n") for c in self._contents]
        (self._index_path / "contents.txt").write_text(
            "\n".join(escaped), encoding="utf-8"
        )
        (self._index_path / "meta.json").write_text(
            json.dumps(self._meta), encoding="utf-8"
        )

    def load(self) -> bool:
        """Load embeddings from disk. Returns True on success."""
        if self._index_path is None or not _NP_AVAILABLE:
            return False
        import json

        emb_file = self._index_path / "embeddings.npy"
        ids_file = self._index_path / "doc_ids.txt"
        if not emb_file.exists() or not ids_file.exists():
            return False

        try:
            self._embeddings = np.load(str(emb_file))
            self._doc_ids = ids_file.read_text(encoding="utf-8").splitlines()

            contents_file = self._index_path / "contents.txt"
            if contents_file.exists():
                escaped = contents_file.read_text(encoding="utf-8").splitlines()
                self._contents = [c.replace("\\n", "\n").replace("\\\\", "\\") for c in escaped]
            else:
                self._contents = [""] * len(self._doc_ids)

            meta_file = self._index_path / "meta.json"
            if meta_file.exists():
                self._meta = json.loads(meta_file.read_text(encoding="utf-8"))
            else:
                self._meta = [{} for _ in self._doc_ids]

            self._loaded = True
            if _FAISS_AVAILABLE:
                self._build_faiss()
            return True
        except Exception as e:
            logger.warning("Failed to load vector index from %s: %s", self._index_path, e)
            return False
