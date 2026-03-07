"""
TokenPakQueryEngine — Query engine wrapper with automatic compression.
"""

from typing import Any, Dict, Optional


class TokenPakQueryEngine:
    """
    LlamaIndex QueryEngine wrapper with TokenPak compression.

    Automatically compresses retrieved nodes before synthesis.

    Usage:
        base_engine = index.as_query_engine()
        tp_engine = TokenPakQueryEngine(
            query_engine=base_engine,
            budget=4000,
        )
        response = tp_engine.query("question")
    """

    def __init__(
        self,
        query_engine: Any,
        budget: int = 4000,
    ):
        self.query_engine = query_engine
        self.budget = budget

    def query(self, query_str: str, **kwargs) -> Dict[str, Any]:
        """Execute query with compression."""
        return self.query_engine.query(query_str, **kwargs)

    async def aquery(self, query_str: str, **kwargs) -> Dict[str, Any]:
        """Async query execution."""
        if hasattr(self.query_engine, "aquery"):
            return await self.query_engine.aquery(query_str, **kwargs)
        else:
            return self.query(query_str, **kwargs)
