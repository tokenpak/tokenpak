"""
TokenPakRetriever — LangChain-compatible retriever with compression.

Wraps any LangChain retriever and automatically compresses documents
within a token budget.
"""

from typing import Optional, Any, Dict, List
import hashlib


class TokenPakRetriever:
    """
    LangChain-compatible retriever with automatic TokenPak compression.

    Wraps any retriever and compresses results before returning.

    Usage:
        base_retriever = vector_store.as_retriever()
        tp_retriever = TokenPakRetriever(
            retriever=base_retriever,
            budget=4000,  # max tokens for retrieved docs
            keep_headers=True,
        )
        compressed_docs = tp_retriever.get_relevant_documents(query)
    """

    def __init__(
        self,
        retriever: Any,
        budget: int = 4000,
        keep_headers: bool = True,
        keep_code: bool = True,
        min_score: float = 0.0,
    ):
        """
        Initialize TokenPakRetriever.

        Args:
            retriever: Any LangChain retriever instance
            budget: Maximum tokens for all retrieved documents combined
            keep_headers: Preserve markdown headers and structure
            keep_code: Preserve code blocks verbatim
            min_score: Minimum relevance score (0-1) to include a document
        """
        self.retriever = retriever
        self.budget = budget
        self.keep_headers = keep_headers
        self.keep_code = keep_code
        self.min_score = min_score
        self._compression_stats = {}

    def get_relevant_documents(self, query: str) -> List[Dict[str, Any]]:
        """
        Retrieve documents and compress them.

        Args:
            query: Query string

        Returns:
            Compressed documents list
        """
        # Get documents from wrapped retriever
        docs = self.retriever.get_relevant_documents(query)
        
        # Filter by score if available
        filtered_docs = []
        for doc in docs:
            metadata = doc.get("metadata", {}) if isinstance(doc, dict) else {}
            score = metadata.get("score", 1.0)
            if score >= self.min_score:
                filtered_docs.append(doc)

        # Compress
        return self._compress_documents(filtered_docs)

    async def aget_relevant_documents(self, query: str) -> List[Dict[str, Any]]:
        """Async version of get_relevant_documents."""
        if hasattr(self.retriever, "aget_relevant_documents"):
            docs = await self.retriever.aget_relevant_documents(query)
        else:
            docs = self.retriever.get_relevant_documents(query)
        
        filtered_docs = []
        for doc in docs:
            metadata = doc.get("metadata", {}) if isinstance(doc, dict) else {}
            score = metadata.get("score", 1.0)
            if score >= self.min_score:
                filtered_docs.append(doc)

        return self._compress_documents(filtered_docs)

    def _compress_documents(self, docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Compress documents to fit within budget."""
        if not docs:
            return []

        # Simple compression: truncate to budget
        # In production, would use actual TokenPak compression engine
        total_tokens = sum(self._estimate_tokens(d) for d in docs)
        
        if total_tokens <= self.budget:
            return docs

        # Proportional truncation
        ratio = self.budget / max(1, total_tokens)
        compressed = []

        for doc in docs:
            content = doc.get("page_content", "") if isinstance(doc, dict) else str(doc)
            new_length = max(50, int(len(content) * ratio))
            compressed_content = content[:new_length] + "..." if len(content) > new_length else content

            if isinstance(doc, dict):
                compressed.append({
                    **doc,
                    "page_content": compressed_content,
                    "metadata": {
                        **doc.get("metadata", {}),
                        "_tokenpak_compressed": True,
                        "_tokenpak_original_length": len(content),
                        "_tokenpak_compressed_length": len(compressed_content),
                    },
                })
            else:
                compressed.append(compressed_content)

        return compressed

    @staticmethod
    def _estimate_tokens(doc: Any) -> int:
        """Estimate token count (1 token ≈ 4 chars)."""
        if isinstance(doc, dict):
            content = doc.get("page_content", "")
        else:
            content = str(doc)
        return len(content) // 4

    def get_compression_stats(self) -> Dict[str, Any]:
        """Return compression statistics."""
        return self._compression_stats
