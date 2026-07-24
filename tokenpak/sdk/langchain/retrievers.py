"""Budget-aware retriever wrapper for LangChain integrations."""

from __future__ import annotations

__all__ = (
    "TokenPakRetriever",
    "blocks_to_docs",
    "doc_to_block",
)


from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .converters import Block, blocks_to_docs, doc_to_block


class _Retriever(Protocol):
    def get_relevant_documents(self, query: str) -> Sequence[object]: ...


@runtime_checkable
class _AsyncRetriever(Protocol):
    async def aget_relevant_documents(self, query: str) -> Sequence[object]: ...


class TokenPakRetriever:
    def __init__(
        self,
        retriever: _Retriever,
        budget: int = 4000,
        avg_tokens_per_char: float = 0.25,
    ) -> None:
        self.retriever = retriever
        self.budget = budget
        self.avg_tokens_per_char = avg_tokens_per_char

    def _estimate_tokens(self, text: str) -> int:
        return max(1, int(len(text) * self.avg_tokens_per_char))

    def _apply_budget(self, blocks: Sequence[Block]) -> list[Block]:
        sorted_blocks = sorted(blocks, key=lambda b: b.priority, reverse=True)
        selected: list[Block] = []
        used = 0
        for block in sorted_blocks:
            tokens = self._estimate_tokens(block.content)
            if used + tokens <= self.budget:
                block.token_count = tokens
                selected.append(block)
                used += tokens
        return selected

    def get_relevant_documents(self, query: str) -> list[object]:
        raw_docs = self.retriever.get_relevant_documents(query)
        return blocks_to_docs(self._apply_budget([doc_to_block(d) for d in raw_docs]))

    async def aget_relevant_documents(self, query: str) -> list[object]:
        if isinstance(self.retriever, _AsyncRetriever):
            try:
                raw_docs = await self.retriever.aget_relevant_documents(query)
            except AttributeError:
                raw_docs = self.retriever.get_relevant_documents(query)
        else:
            raw_docs = self.retriever.get_relevant_documents(query)
        return blocks_to_docs(self._apply_budget([doc_to_block(d) for d in raw_docs]))
