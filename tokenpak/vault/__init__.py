"""TokenPak vault package — re-exports from agent.vault for compatibility."""
import os as _os

# Canonical path to the vault-editable install root.
# Transferred from monolith (TPK-CONSOLIDATION-A2a, line 60).
# Used by the monolith's sys.path fixup and by vault indexer path resolution.
_VAULT_TOKENPAK: str = _os.path.expanduser("~/.tokenpak/vault")

try:
    from tokenpak.vault.query_expansion import (
        expand_query,
        get_query_terms_with_weights,
        stem_token,
        tokenize,
    )
except ImportError:
    pass

try:
    from tokenpak.vault.backend_protocol import RetrievalBackend, SemanticScorer
except ImportError:
    pass

__all__ = ['ast_parser', 'backend_protocol', 'blocks', 'chunk_shapes', 'chunk_shaping', 'health', 'indexer', 'progressive_disclosure', 'query_expansion', 'retrieval', 'scoring', 'search', 'slicer', 'sqlite_backend', 'symbol_extraction', 'symbols', 'watcher']
