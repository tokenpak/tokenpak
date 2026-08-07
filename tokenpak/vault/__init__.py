"""TokenPak vault package — re-exports from agent.vault for compatibility."""

import os as _os


# Canonical path to the vault-editable install root.
# Transferred from the legacy monolith (line 60).
# Used by the monolith's sys.path fixup and by vault indexer path resolution.
def _vault_default() -> str:
    """Vault directory, resolved at call time across homes."""
    from tokenpak import _paths

    found = _paths.resolve_existing("vault")
    return str(found if found is not None else _paths.write_home() / "vault")


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

__all__ = [
    "ast_parser",
    "backend_protocol",
    "blocks",
    "chunk_shapes",
    "chunk_shaping",
    "health",
    "indexer",
    "progressive_disclosure",
    "query_expansion",
    "retrieval",
    "scoring",
    "search",
    "slicer",
    "sqlite_backend",
    "symbol_extraction",
    "symbols",
    "watcher",
]
