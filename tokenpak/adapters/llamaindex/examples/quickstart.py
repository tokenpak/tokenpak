"""
LlamaIndex TokenPak Adapter — Quickstart

Demonstrates budget-aware node compression for RAG pipelines.
No LlamaIndex installation required — uses mock engines.

For full LlamaIndex integration:
    pip install tokenpak[llamaindex]
"""

from tokenpak.adapters.llamaindex import (
    TokenPakSynthesizer,
    TokenPakQueryEngine,
    MultiIndexFusion,
    llamaindex_node_to_block,
)


def demo_node_conversion() -> None:
    print("=" * 60)
    print("Demo 1: Node → Block conversion")
    print("=" * 60)

    node = {
        "id": "doc_001",
        "text": "TokenPak is a context compression protocol for LLM pipelines.",
        "metadata": {"file_name": "tokenpak_intro.md", "page": 1},
        "score": 0.92,
    }

    block = llamaindex_node_to_block(node, block_type="evidence")
    print(f"Block ID      : {block.id}")
    print(f"Quality       : {block.quality}")
    print(f"Tokens        : {block.tokens}")
    print(f"Provenance    : {block.provenance}")
    print()


def demo_synthesizer() -> None:
    print("=" * 60)
    print("Demo 2: TokenPakSynthesizer — compress nodes before synthesis")
    print("=" * 60)

    synthesizer = TokenPakSynthesizer(budget=500)

    nodes = [
        {
            "id": f"doc_{i}",
            "text": f"Document {i}: " + "Relevant content about TokenPak. " * 50,
            "metadata": {"file_name": f"doc{i}.pdf"},
            "score": 0.95 - i * 0.1,
        }
        for i in range(5)
    ]

    result = synthesizer.synthesize("What is TokenPak?", nodes)
    stats = result["compression_stats"]

    print(f"Input tokens  : {stats['input_tokens']}")
    print(f"Output tokens : {stats['output_tokens']}")
    print(f"Ratio         : {stats['compression_ratio']:.1%}")
    print(f"Blocks out    : {stats['output_blocks']}")
    print()


def demo_query_engine() -> None:
    print("=" * 60)
    print("Demo 3: TokenPakQueryEngine — wrap any query engine")
    print("=" * 60)

    class MockEngine:
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
                    return "Synthesized answer."

            return Resp()

        async def aquery(self, q, **kw):
            return self.query(q)

    tp_engine = TokenPakQueryEngine(query_engine=MockEngine(), budget=800)
    pack = tp_engine.query_as_tokenpak("How does context compression work?")

    print(f"Query         : {pack['query']}")
    print(f"Blocks        : {len(pack['blocks'])}")
    print(f"Input tokens  : {pack['tokens']['input']}")
    print(f"Output tokens : {pack['tokens']['output']}")
    print(f"Ratio         : {pack['tokens']['ratio']:.1%}")
    print()


def demo_multi_index_fusion() -> None:
    print("=" * 60)
    print("Demo 4: MultiIndexFusion — fuse multiple indexes")
    print("=" * 60)

    def make_engine(name, n=3):
        class Eng:
            def query(self, q, **kw):
                class R:
                    source_nodes = [
                        {
                            "id": f"{name}_{i}",
                            "text": f"[{name}] doc {i}: " + f"content from {name} " * 30,
                            "metadata": {"source": name},
                            "score": 0.9 - i * 0.1,
                        }
                        for i in range(n)
                    ]

                return R()

            async def aquery(self, q, **kw):
                return self.query(q)

        return Eng()

    fusion = MultiIndexFusion(
        indexes={"docs": make_engine("docs", 4), "code": make_engine("code", 3)},
        budget=1200,
    )
    pack = fusion.query_as_tokenpak("Explain TokenPak protocol")

    print(f"Indexes       : {', '.join(pack['metadata']['index_names'])}")
    print(f"Total blocks  : {len(pack['blocks'])}")
    print(f"Input tokens  : {pack['tokens']['input']}")
    print(f"Output tokens : {pack['tokens']['output']}")
    print(f"Ratio         : {pack['tokens']['ratio']:.1%}")
    print()


if __name__ == "__main__":
    demo_node_conversion()
    demo_synthesizer()
    demo_query_engine()
    demo_multi_index_fusion()
    print("Quickstart complete.")
