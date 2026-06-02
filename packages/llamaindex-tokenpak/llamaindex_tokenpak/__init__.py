"""
llamaindex-tokenpak

TokenPak integration for LlamaIndex — automatic context compression for RAG pipelines.

Reduces token costs on retrieved nodes while preserving structure; measure your savings with `tokenpak savings`.

Quick Start:
    from llamaindex_tokenpak import TokenPakSynthesizer, TokenPakQueryEngine

    # Compress query engine results
    synthesizer = TokenPakSynthesizer(budget=4000)
    result = synthesizer.synthesize("question", nodes=nodes)

    # Wrap any query engine
    tp_engine = TokenPakQueryEngine(query_engine=base_engine, budget=4000)
    pack = tp_engine.query_as_tokenpak("What is context compression?")

    # Fuse multiple indexes
    from llamaindex_tokenpak import MultiIndexFusion
    fusion = MultiIndexFusion({"docs": docs_engine, "code": code_engine}, budget=6000)
    result = fusion.query("How does compression work?")
"""

from .converters import (
    LlamaBlock,
    Node,  # backward compat alias
    llamaindex_node_to_block,
    block_to_llamaindex_node,
    llamaindex_nodes_to_blocks,
    blocks_to_llamaindex_nodes,
)
from .synthesizer import TokenPakSynthesizer
from .query_engine import TokenPakQueryEngine
from .index import TokenPakIndex
from .fusion import MultiIndexFusion

__version__ = "0.2.0"
__all__ = [
    # Block conversion
    "LlamaBlock",
    "Node",
    "llamaindex_node_to_block",
    "block_to_llamaindex_node",
    "llamaindex_nodes_to_blocks",
    "blocks_to_llamaindex_nodes",
    # Core classes
    "TokenPakSynthesizer",
    "TokenPakQueryEngine",
    "TokenPakIndex",
    "MultiIndexFusion",
]
