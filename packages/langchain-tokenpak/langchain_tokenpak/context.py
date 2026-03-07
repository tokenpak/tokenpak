"""
TokenPakContextManager — Manages compression budget across retriever + memory.

Coordinates token budgets between retrieved documents and chat history
to maximize relevant context within a fixed token limit.
"""

from typing import Optional, Any, Dict, List


class TokenPakContextManager:
    """
    Manages compression budgets for documents + memory in LangChain chains.

    Splits a total context budget between retrieved documents and chat
    history, and applies compression rules to both.

    Usage:
        ctx_mgr = TokenPakContextManager(
            total_budget=8000,
            doc_ratio=0.7,  # 70% for retrieved docs
        )
        doc_budget = ctx_mgr.document_budget()
        memory_budget = ctx_mgr.memory_budget()
    """

    def __init__(
        self,
        total_budget: int = 8000,
        doc_ratio: float = 0.7,
        min_memory_tokens: int = 500,
    ):
        """
        Initialize TokenPakContextManager.

        Args:
            total_budget: Total token budget for context
            doc_ratio: Fraction of budget for documents (0-1), rest for memory
            min_memory_tokens: Minimum tokens reserved for memory
        """
        self.total_budget = total_budget
        self.doc_ratio = max(0.0, min(1.0, doc_ratio))
        self.min_memory_tokens = min_memory_tokens

    def document_budget(self) -> int:
        """Get token budget for retrieved documents."""
        return int(self.total_budget * self.doc_ratio)

    def memory_budget(self) -> int:
        """Get token budget for chat history."""
        return max(
            self.min_memory_tokens,
            self.total_budget - self.document_budget(),
        )

    def adjust_budget(self, doc_tokens: int, memory_tokens: int) -> Dict[str, int]:
        """
        Dynamically adjust budget based on actual usage.

        Args:
            doc_tokens: Actual tokens used by documents
            memory_tokens: Actual tokens used by memory

        Returns:
            New allocation {doc_budget, memory_budget}
        """
        total_used = doc_tokens + memory_tokens
        
        if total_used <= self.total_budget:
            # Both fit comfortably, no adjustment needed
            return {
                "doc_budget": self.document_budget(),
                "memory_budget": self.memory_budget(),
            }

        # Adjust ratios based on actual usage
        doc_priority = doc_tokens / max(1, total_used)
        new_doc_budget = int(self.total_budget * doc_priority)
        new_memory_budget = self.total_budget - new_doc_budget

        # Ensure minimum memory
        if new_memory_budget < self.min_memory_tokens:
            new_memory_budget = self.min_memory_tokens
            new_doc_budget = self.total_budget - new_memory_budget

        return {
            "doc_budget": new_doc_budget,
            "memory_budget": new_memory_budget,
        }

    def __repr__(self) -> str:
        return (
            f"TokenPakContextManager("
            f"total={self.total_budget}, "
            f"docs={self.document_budget()}, "
            f"memory={self.memory_budget()})"
        )
