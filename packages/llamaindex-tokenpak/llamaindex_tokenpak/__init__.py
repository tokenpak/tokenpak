"""
llamaindex-tokenpak

TokenPak integration for LlamaIndex — automatic context compression for RAG pipelines.

Quick Start:
    from llamaindex_tokenpak import TokenPakSynthesizer, TokenPakIndex

    # Compress query engine results
    synthesizer = TokenPakSynthesizer(budget=4000)
    response = query_engine.query("question", synthesizer=synthesizer)

    # Create index with compression
    index = TokenPakIndex.from_documents(docs, budget=2000)
"""

from .converters import (
    Node,
    llamaindex_node_to_block,
    block_to_llamaindex_node,
)
from .synthesizer import TokenPakSynthesizer
from .query_engine import TokenPakQueryEngine
from .index import TokenPakIndex

__version__ = "0.1.0"
__all__ = [
    "Node",
    "TokenPakSynthesizer",
    "TokenPakQueryEngine",
    "TokenPakIndex",
    "llamaindex_node_to_block",
    "block_to_llamaindex_node",
]
