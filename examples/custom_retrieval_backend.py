"""
Example: Custom Retrieval Backend (Replace mode)
=================================================

This example shows the minimum implementation needed to replace TokenPak's
built-in BM25 backend with your own search infrastructure.

Usage::

    # In your shell or .env:
    TOKENPAK_RETRIEVAL_BACKEND=custom:examples.custom_retrieval_backend.InMemoryBackend

When this env var is set, TokenPak will load InMemoryBackend instead of
running BM25. All search calls go to your backend.

To use a real backend (pgvector, Elasticsearch, etc.), replace the
``search()`` implementation with your actual search logic.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from tokenpak.vault.backend_protocol import RetrievalBackendBase


class InMemoryBackend(RetrievalBackendBase):
    """Simple in-memory retrieval backend for demonstration.

    Stores blocks in a dict and does basic substring matching.
    In production, replace ``search()`` with your real search logic.

    Args:
        vault_path: Path to the vault index directory.
            Passed by TokenPak automatically when loading the backend.
            Use it to locate your index files if needed.
    """

    def __init__(self, vault_path: str) -> None:
        self._vault_path = vault_path
        self._blocks: Dict[str, dict] = {}
        self._load_blocks()

    def _load_blocks(self) -> None:
        """Load blocks from in-memory store. Replace with real index loading."""
        # Example: hardcoded blocks for demo purposes
        demo_blocks = [
            {
                "block_id": "readme-intro",
                "source_path": "README.md",
                "content": "TokenPak is an LLM proxy with context compression.",
                "raw_tokens": 12,
            },
            {
                "block_id": "quickstart-install",
                "source_path": "docs/quickstart.md",
                "content": "Install TokenPak: pip install tokenpak",
                "raw_tokens": 8,
            },
            {
                "block_id": "api-compression",
                "source_path": "docs/api.md",
                "content": "The compression pipeline reduces prompt tokens by 40-60%.",
                "raw_tokens": 12,
            },
        ]
        for block in demo_blocks:
            self._blocks[block["block_id"]] = block

    # ------------------------------------------------------------------
    # Required: available
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """Return True when the backend is ready to serve queries."""
        return len(self._blocks) > 0

    # ------------------------------------------------------------------
    # Required: maybe_reload
    # ------------------------------------------------------------------

    def maybe_reload(self) -> None:
        """Check for index updates and reload if needed.

        Called every 5 minutes by TokenPak's reload timer.
        For a real backend, check file mtimes or poll your index.
        """
        # For this demo, nothing to reload
        pass

    # ------------------------------------------------------------------
    # Required: search
    # ------------------------------------------------------------------

    def search(
        self, query: str, top_k: int = 5, min_score: float = 2.0
    ) -> List[Tuple[dict, float]]:
        """Search for relevant blocks.

        This demo uses simple substring matching.
        Replace with BM25, vector search, or your preferred method.

        Args:
            query: Natural language search query.
            top_k: Maximum number of results to return.
            min_score: Minimum relevance score. Results below this are excluded.

        Returns:
            List of (block_dict, score) tuples, sorted by score descending.
        """
        query_terms = query.lower().split()
        scored: List[Tuple[dict, float]] = []

        for block in self._blocks.values():
            content_lower = block["content"].lower()
            # Simple scoring: count how many query terms appear in content
            matches = sum(1 for term in query_terms if term in content_lower)
            if matches > 0:
                # Normalize to a BM25-like scale
                score = float(matches * 3)
                if score >= min_score:
                    scored.append((block, score))

        # Sort by score descending, return top_k
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    # ------------------------------------------------------------------
    # Optional: compile_injection (inherited from RetrievalBackendBase)
    # ------------------------------------------------------------------
    # RetrievalBackendBase provides compile_injection() for free.
    # It calls self.search() and formats results within the token budget.
    # Override only if you need custom formatting.


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    backend = InMemoryBackend(vault_path="/tmp/demo-vault")
    print(f"Available: {backend.available}")

    results = backend.search("compression pipeline", top_k=3)
    print(f"\nSearch results for 'compression pipeline': {len(results)} hits")
    for block, score in results:
        print(f"  [{score:.1f}] {block['source_path']}: {block['content'][:60]}...")

    text, tokens, refs = backend.compile_injection("tokenpak install", budget=500)
    print(f"\nInjection: {tokens} tokens, {len(refs)} sources")
    print(text[:300])
