"""TokenPak Agent Vault — local file indexing, AST parsing, and block storage."""

from .ast_parser import ASTParser
from .blocks import BlockRecord, BlockStore, SliceStore, get_block_store
from .indexer import VaultIndexer
from .slicer import SliceRecord, detect_split_strategy, should_slice, slice_content
from .symbols import Symbol, SymbolTable

__all__ = [
    "VaultIndexer",
    "BlockStore",
    "BlockRecord",
    "SliceStore",
    "get_block_store",
    "SymbolTable",
    "Symbol",
    "ASTParser",
    "SliceRecord",
    "slice_content",
    "should_slice",
    "detect_split_strategy",
]
