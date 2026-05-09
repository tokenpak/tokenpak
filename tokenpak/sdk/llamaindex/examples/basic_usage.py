"""
Basic usage examples for llamaindex-tokenpak.

Run with: python examples/basic_usage.py
(No LlamaIndex installation required — uses mock engines.)
"""

from llamaindex_tokenpak import (
    MultiIndexFusion,
    TokenPakQueryEngine,
    TokenPakSynthesizer,
    llamaindex_node_to_block,
)

# ---------------------------------------------------------------------------
# Example 1: Node ↔ Block conversion
# ---------------------------------------------------------------------------

print("=" * 60)
print("Example 1: Node → Block conversion")
print("=" * 60)

node = {
    "id": "doc_001",
    "text": "TokenPak is a context compression protocol for LLM pipelines.",
    "metadata": {"file_name": "tokenpak_intro.md", "page": 1},
    "score": 0.92,
}

block = llamaindex_node_to_block(node, block_type="evidence")
print(f"Block ID:      {block.id}")
print(f"Quality:       {block.quality}")
print(f"Tokens:        {block.tokens}")
print(f"Provenance:    {block.provenance}")
print(f"Block dict:    {block.to_tokenpak_dict()}")
print()


# ---------------------------------------------------------------------------
# Example 2: TokenPakSynthesizer — compress nodes before synthesis
# ---------------------------------------------------------------------------

print("=" * 60)
print("Example 2: TokenPakSynthesizer")
print("=" * 60)

synthesizer = TokenPakSynthesizer(budget=500)

# Simulate 5 retrieved nodes
nodes = [
    {
        "id": f"doc_{i}",
        "text": f"Document {i}: " + "This is relevant content about TokenPak. " * 50,
        "metadata": {"file_name": f"doc{i}.pdf"},
        "score": 0.95 - i * 0.1,
    }
    for i in range(5)
]

result = synthesizer.synthesize("What is TokenPak?", nodes)
stats = result["compression_stats"]

print(f"Input tokens:  {stats['input_tokens']}")
print(f"Output tokens: {stats['output_tokens']}")
print(f"Ratio:         {stats['compression_ratio']:.1%}")
print(f"Blocks out:    {stats['output_blocks']}")
print()
print("--- Compressed context (first 500 chars) ---")
print(result["response"][:500])
print()


# ---------------------------------------------------------------------------
# Example 3: TokenPakQueryEngine — wrap any engine
# ---------------------------------------------------------------------------

print("=" * 60)
print("Example 3: TokenPakQueryEngine with query_as_tokenpak()")
print("=" * 60)


class MockEngine:
    """Simulates a LlamaIndex query engine."""

    def query(self, q, **kw):
        class Resp:
            source_nodes = [
                {
                    "id": f"n{i}",
                    "text": f"Source {i}: " + "evidence text " * 100,
                    "metadata": {"source": f"paper_{i}.pdf"},
                    "score": 0.9 - i * 0.05,
                }
                for i in range(6)
            ]

            def __str__(self):
                return "Synthesized answer based on sources."

        return Resp()

    async def aquery(self, q, **kw):
        return self.query(q)


engine = MockEngine()
tp_engine = TokenPakQueryEngine(query_engine=engine, budget=800)

pack = tp_engine.query_as_tokenpak("How does context compression work?")

print(f"Query:         {pack['query']}")
print(f"Blocks:        {len(pack['blocks'])}")
print(f"Input tokens:  {pack['tokens']['input']}")
print(f"Output tokens: {pack['tokens']['output']}")
print(f"Budget:        {pack['tokens']['budget']}")
print(f"Ratio:         {pack['tokens']['ratio']:.1%}")
print()
print("--- Context snippet ---")
print(pack["context"][:400])
print()


# ---------------------------------------------------------------------------
# Example 4: MultiIndexFusion — fuse multiple indexes
# ---------------------------------------------------------------------------

print("=" * 60)
print("Example 4: MultiIndexFusion")
print("=" * 60)


def make_engine(name, node_count=3):
    class Eng:
        def query(self, q, **kw):
            class R:
                source_nodes = [
                    {
                        "id": f"{name}_{i}",
                        "text": f"[{name}] doc {i}: " + f"content from {name} " * 30,
                        "metadata": {"source": name, "doc_id": i},
                        "score": 0.9 - i * 0.1,
                    }
                    for i in range(node_count)
                ]

            return R()

        async def aquery(self, q, **kw):
            return self.query(q)

    return Eng()


indexes = {
    "documentation": make_engine("docs", 4),
    "codebase": make_engine("code", 3),
    "wiki": make_engine("wiki", 2),
}

fusion = MultiIndexFusion(
    indexes=indexes,
    budget=1500,
    strategy="weighted",
    weights={"documentation": 0.5, "codebase": 0.3, "wiki": 0.2},
)

pack = fusion.query_as_tokenpak("Explain the TokenPak protocol")

print(f"Strategy:      {pack['metadata']['strategy']}")
print(f"Indexes:       {', '.join(pack['metadata']['index_names'])}")
print(f"Sources:       {pack['sources']}")
print(f"Total blocks:  {len(pack['blocks'])}")
print(f"Input tokens:  {pack['tokens']['input']}")
print(f"Output tokens: {pack['tokens']['output']}")
print(f"Ratio:         {pack['tokens']['ratio']:.1%}")
print()
print("--- Fused context snippet ---")
print(pack["context"][:500])
print()

print("=" * 60)
print("All examples complete.")
print("=" * 60)
