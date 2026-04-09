"""Example: Custom semantic scorer (Augment mode).

Usage:
    TOKENPAK_SEMANTIC_BACKEND=custom:examples.custom_semantic_scorer.CosineSimilarityScorer

In Augment mode, BM25 runs normally, then this scorer provides additional
semantic similarity scores that fuse with BM25 via the multi-signal scorer
(0.45 BM25 + 0.45 semantic + 0.10 meta).

Replace the scoring logic with your own (pgvector embeddings, sentence-transformers, etc.).
"""

from typing import Dict, List


class CosineSimilarityScorer:
    """Example semantic scorer using simple TF-IDF cosine similarity.
    
    In production, replace this with:
    - pgvector: query your embedding table for similarity scores
    - Qdrant/Weaviate: use their native similarity search
    - sentence-transformers: compute embeddings on the fly
    """

    def __init__(self):
        self._cache = {}  # Simple embedding cache

    def score(self, query: str, block_ids: List[str]) -> Dict[str, float]:
        """Return semantic similarity scores for given blocks.
        
        Args:
            query: Natural language search query.
            block_ids: Block IDs from BM25 results to score.
            
        Returns:
            Dict mapping block_id -> similarity score in [0.0, 1.0].
        """
        # Replace this with your actual embedding-based similarity logic
        # Example with pgvector:
        #   query_embedding = self.model.encode(query)
        #   rows = self.conn.execute(
        #       "SELECT block_id, 1 - (embedding <=> %s) AS score "
        #       "FROM vault_blocks WHERE block_id = ANY(%s)",
        #       (query_embedding, block_ids)
        #   ).fetchall()
        #   return {r[0]: r[1] for r in rows}
        
        # Placeholder: return 0.5 for all blocks
        # This demonstrates the interface without requiring any dependencies
        return {bid: 0.5 for bid in block_ids}
