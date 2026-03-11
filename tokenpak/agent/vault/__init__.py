"""TokenPak Agent Vault — local file indexing, AST parsing, and block storage."""

from .ast_parser import ASTParser
from .blocks import BlockRecord, BlockStore, get_block_store
from .chunk_shapes import CHUNK_SHAPES, apply_shape, get_shape_for_intent, reshape_chunks
from .indexer import VaultIndexer
from .symbols import Symbol, SymbolTable

__all__ = [
    "VaultIndexer",
    "BlockStore",
    "BlockRecord",
    "get_block_store",
    "SymbolTable",
    "Symbol",
    "ASTParser",
    "CHUNK_SHAPES",
    "apply_shape",
    "get_shape_for_intent",
    "reshape_chunks",
]
