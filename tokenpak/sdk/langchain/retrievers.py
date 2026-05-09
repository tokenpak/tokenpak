from .converters import blocks_to_docs, doc_to_block


class TokenPakRetriever:
    def __init__(self, retriever, budget=4000, avg_tokens_per_char=0.25):
        self.retriever = retriever
        self.budget = budget
        self.avg_tokens_per_char = avg_tokens_per_char

    def _estimate_tokens(self, text):
        return max(1, int(len(text) * self.avg_tokens_per_char))

    def _apply_budget(self, blocks):
        sorted_blocks = sorted(blocks, key=lambda b: b.priority, reverse=True)
        selected, used = [], 0
        for block in sorted_blocks:
            tokens = self._estimate_tokens(block.content)
            if used + tokens <= self.budget:
                block.token_count = tokens
                selected.append(block)
                used += tokens
        return selected

    def get_relevant_documents(self, query):
        raw_docs = self.retriever.get_relevant_documents(query)
        return blocks_to_docs(self._apply_budget([doc_to_block(d) for d in raw_docs]))

    async def aget_relevant_documents(self, query):
        try:
            raw_docs = await self.retriever.aget_relevant_documents(query)
        except AttributeError:
            raw_docs = self.retriever.get_relevant_documents(query)
        return blocks_to_docs(self._apply_budget([doc_to_block(d) for d in raw_docs]))
