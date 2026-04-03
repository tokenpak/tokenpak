"""
Example: Custom Semantic Scorer (Augment mode)
===============================================

This example shows how to add semantic similarity scoring on top of
TokenPak's built-in BM25 retrieval without replacing it.

In Augment mode:
1. BM25 runs as normal and returns candidate blocks
2. Your scorer receives the query + candidate block IDs
3. It returns similarity scores for those candidates
4. TokenPak fuses both signals: 0.45 × BM25 + 0.45 × semantic + 0.10 × meta

Usage::

    # In your shell or .env:
    TOKENPAK_SEMANTIC_BACKEND=custom:examples.custom_semantic_scorer.KeywordScorer

When this env var is set, TokenPak calls your scorer after every BM25 search.
High semantic scores elevate relevant blocks even if BM25 scored them lower.

For production, replace the keyword-overlap logic with real embeddings
(sentence-transformers, OpenAI text-embedding-3-small, etc.).
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Dict, List


class KeywordScorer:
    """Keyword-overlap semantic scorer.

    Computes similarity as the cosine similarity of TF-IDF-like term vectors
    between the query and each candidate block's content.

    This is a self-contained scorer requiring no external dependencies —
    useful for development/testing. For production, use real embeddings.

    No constructor arguments needed — TokenPak instantiates with ``cls()``.
    """

    def __init__(self) -> None:
        # In a real scorer, you'd initialize your embedding model here.
        # Example:
        #   from sentence_transformers import SentenceTransformer
        #   self.model = SentenceTransformer("all-MiniLM-L6-v2")
        pass

    def score(self, query: str, block_ids: List[str]) -> Dict[str, float]:
        """Return semantic similarity scores for given blocks.

        This demo implementation uses keyword overlap (TF cosine similarity).
        Replace with real embedding similarity for production use.

        Args:
            query: The search query from the user.
            block_ids: Block IDs from BM25 results. Use these to look up
                       the block content from your index or embedding store.

        Returns:
            Dict mapping block_id → similarity score in [0.0, 1.0].
            Missing block_ids are treated as 0.0 by TokenPak.

        Note:
            This example returns a constant score per block since we don't
            have access to block content from the scorer. In production,
            you'd load embeddings from your vector store using block_ids.
        """
        if not query or not block_ids:
            return {}

        query_terms = self._tokenize(query)
        if not query_terms:
            return {}

        # In production, you'd retrieve each block's embedding by block_id
        # from your vector store and compute cosine similarity.
        #
        # Example with pgvector:
        #   embeddings = self.db.get_embeddings(block_ids)
        #   query_emb = self.model.encode(query)
        #   scores = {bid: cosine_sim(query_emb, emb) for bid, emb in embeddings}
        #
        # For this demo, we simulate by extracting hints from block_id names:
        scores: Dict[str, float] = {}
        for block_id in block_ids:
            # Fake semantic signal: check if block_id terms overlap with query
            bid_terms = self._tokenize(block_id.replace("-", " ").replace("_", " "))
            similarity = self._term_overlap_score(query_terms, bid_terms)
            scores[block_id] = max(0.0, min(1.0, similarity))

        return scores

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _tokenize(self, text: str) -> Counter:
        """Tokenize text into a term-frequency Counter."""
        tokens = re.findall(r"\b[a-z]{2,}\b", text.lower())
        return Counter(tokens)

    def _term_overlap_score(self, a: Counter, b: Counter) -> float:
        """Cosine similarity between two term-frequency vectors."""
        if not a or not b:
            return 0.0

        # Dot product
        dot = sum(a[t] * b[t] for t in a if t in b)
        if dot == 0:
            return 0.0

        # Magnitudes
        mag_a = math.sqrt(sum(v * v for v in a.values()))
        mag_b = math.sqrt(sum(v * v for v in b.values()))

        if mag_a == 0 or mag_b == 0:
            return 0.0

        return dot / (mag_a * mag_b)


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    scorer = KeywordScorer()

    query = "python compression tutorial"
    block_ids = [
        "python-quickstart",
        "compression-pipeline",
        "machine-learning-intro",
        "tokenpak-install",
    ]

    scores = scorer.score(query, block_ids)
    print(f"Semantic scores for query: '{query}'")
    for bid, score in sorted(scores.items(), key=lambda x: -x[1]):
        bar = "█" * int(score * 20)
        print(f"  {score:.3f} {bar:<20} {bid}")

    print("\nScores are in [0, 1]. Higher = more semantically similar.")
    print("In Augment mode, these fuse with BM25: 0.45×BM25 + 0.45×semantic + 0.10×meta")
