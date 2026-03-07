"""
TokenPakSynthesizer — Automatic compression for LlamaIndex synthesis.

Compresses context nodes before passing to the LLM in query engines.
"""

from typing import List, Dict, Any


class TokenPakSynthesizer:
    """
    LlamaIndex BaseSynthesizer compatible synthesizer with TokenPak compression.

    Automatically compresses retrieved nodes within a token budget.

    Usage:
        synthesizer = TokenPakSynthesizer(budget=4000)
        response = query_engine.query(query, synthesizer=synthesizer)
    """

    def __init__(
        self,
        budget: int = 4000,
        keep_headers: bool = True,
    ):
        self.budget = budget
        self.keep_headers = keep_headers

    def synthesize(
        self,
        query: str,
        nodes: List[Dict[str, Any]],
    ) -> str:
        """
        Synthesize response from compressed nodes.

        Args:
            query: Query string
            nodes: List of LlamaIndex Node dicts

        Returns:
            Synthesized response text
        """
        compressed_nodes = self._compress_nodes(nodes)
        
        # In production, would call LLM here
        # For now, return a placeholder
        return f"Synthesized response to: {query}"

    def _compress_nodes(self, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Compress nodes to fit within budget."""
        if not nodes:
            return []

        total_tokens = sum(self._estimate_tokens(n) for n in nodes)
        
        if total_tokens <= self.budget:
            return nodes

        ratio = self.budget / max(1, total_tokens)
        compressed = []

        for node in nodes:
            content = node.get("text", "")
            new_length = max(50, int(len(content) * ratio))
            compressed_content = content[:new_length] + "..." if len(content) > new_length else content

            compressed.append({
                **node,
                "text": compressed_content,
                "metadata": {
                    **node.get("metadata", {}),
                    "_tokenpak_compressed": True,
                },
            })

        return compressed

    @staticmethod
    def _estimate_tokens(node: Dict[str, Any]) -> int:
        """Estimate token count (1 token ≈ 4 chars)."""
        content = node.get("text", "")
        return len(content) // 4
