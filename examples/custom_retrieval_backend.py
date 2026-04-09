"""Example: Custom retrieval backend (Replace mode).

Usage:
    TOKENPAK_RETRIEVAL_BACKEND=custom:examples.custom_retrieval_backend.InMemoryBackend

This example shows a minimal in-memory backend that satisfies the
RetrievalBackend protocol. Replace the search logic with your own
(pgvector, Elasticsearch, Qdrant, etc.).
"""

from tokenpak.agent.vault.backend_protocol import RetrievalBackendBase


class InMemoryBackend(RetrievalBackendBase):
    """Minimal in-memory retrieval backend example.
    
    In Replace mode, this backend handles ALL retrieval — BM25 is bypassed.
    Implement search() with your own logic; compile_injection() is free.
    """

    def __init__(self, vault_path: str):
        self._vault_path = vault_path
        self._documents = {}  # block_id -> block_dict
        self._ready = True
        self._load_documents()

    def _load_documents(self):
        """Load documents from your data source."""
        # Replace this with your actual data loading logic
        # For example: query pgvector, load from Elasticsearch index, etc.
        import json
        from pathlib import Path
        
        index_file = Path(self._vault_path) / "index.json"
        blocks_dir = Path(self._vault_path) / "blocks"
        
        if index_file.exists():
            with open(index_file) as f:
                data = json.load(f)
            block_meta = data.get("blocks", data) if isinstance(data, dict) else {}
            for bid, meta in (block_meta.items() if isinstance(block_meta, dict) else []):
                block_file = blocks_dir / f"{bid}.txt"
                if block_file.exists():
                    self._documents[bid] = {
                        "block_id": bid,
                        "source_path": meta.get("source_path", ""),
                        "content": block_file.read_text(),
                        "raw_tokens": meta.get("raw_tokens", 0),
                    }

    @property
    def available(self) -> bool:
        return self._ready and len(self._documents) > 0

    def maybe_reload(self) -> None:
        """Reload documents if data source has changed."""
        # Implement your own change-detection logic here
        pass

    def search(self, query, top_k=5, min_score=2.0):
        """Simple keyword search — replace with your search logic."""
        query_terms = set(query.lower().split())
        results = []
        
        for bid, doc in self._documents.items():
            content_lower = doc["content"].lower()
            score = sum(1.0 for t in query_terms if t in content_lower)
            if score >= min_score:
                results.append((doc, score))
        
        results.sort(key=lambda x: -x[1])
        return results[:top_k]
