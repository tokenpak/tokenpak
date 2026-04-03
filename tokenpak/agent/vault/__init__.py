"""TokenPak Agent Vault — local file indexing, AST parsing, and block storage."""

from .ast_parser import ASTParser
from .backend_protocol import (
    RetrievalBackend,
    RetrievalBackendBase,
    SemanticScorer,
    load_custom_backend,
    load_custom_scorer,
)
from .blocks import BlockRecord, BlockStore, SliceStore, get_block_store
from .chunk_shaping import CHUNK_SHAPES, apply_shape, get_shape_for_intent, reshape_chunks
from .indexer import VaultIndexer
from .slicer import SliceRecord, detect_split_strategy, should_slice, slice_content
from .symbol_extraction import Symbol, SymbolTable

__all__ = [
    "VaultIndexer",
    "BlockStore",
    "BlockRecord",
    "SliceStore",
    "get_block_store",
    "SymbolTable",
    "Symbol",
    "ASTParser",
    "CHUNK_SHAPES",
    "apply_shape",
    "get_shape_for_intent",
    "reshape_chunks",
    "SliceRecord",
    "slice_content",
    "should_slice",
    "detect_split_strategy",
    "RetrievalBackend",
    "SemanticScorer",
    "RetrievalBackendBase",
    "load_custom_backend",
    "load_custom_scorer",
]
