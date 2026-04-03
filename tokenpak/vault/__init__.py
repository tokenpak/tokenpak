"""
tokenpak.vault — vault indexing, search, AST parsing, and block storage.

This package promotes tokenpak.agent.vault to a top-level namespace.
All vault functionality is free (V1-V8 per architecture doc).

Public API:
    VaultIndexer        — main indexer for vault directories
    BlockStore          — persistent block storage
    BlockRecord         — individual block record
    SliceStore          — slice storage
    get_block_store     — factory for BlockStore
    SymbolTable         — symbol extraction result
    Symbol              — individual symbol
    ASTParser           — AST-based code parser
    VaultHealth         — vault health checker
    HealthCheckResult   — vault health check result
    IndexStatus         — index status record
    RepairResult        — repair operation result
    CHUNK_SHAPES        — available chunk shapes
    apply_shape         — apply chunk shape
    get_shape_for_intent — select shape by intent
    reshape_chunks      — reshape chunk list
    SliceRecord         — slice record
    slice_content       — slice content into records
    should_slice        — determine if content should be sliced
    detect_split_strategy — detect split strategy for content
    RetrievalBackend    — custom backend protocol
    RetrievalBackendBase — base class for custom backends
    SemanticScorer      — custom scorer protocol
    load_custom_backend — load backend from config
    load_custom_scorer  — load scorer from config

Search utilities (function-based, not class-based):
    from tokenpak.vault.search import inject_retrieved_context, sort_retrieval_results
"""

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
from .health import HealthCheckResult, IndexStatus, RepairResult, VaultHealth
from .indexer import VaultIndexer
from .slicer import SliceRecord, detect_split_strategy, should_slice, slice_content
from .symbol_extraction import Symbol, SymbolTable

__all__ = [
    "VaultIndexer",
    "VaultHealth",
    "HealthCheckResult",
    "IndexStatus",
    "RepairResult",
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
    "RetrievalBackendBase",
    "SemanticScorer",
    "load_custom_backend",
    "load_custom_scorer",
]
