"""TokenPak vault package — re-exports from agent.vault for compatibility."""
import os as _os

This package promotes tokenpak.vault to a top-level namespace.
All vault functionality is free (V1-V8 per architecture doc).

try:
    from tokenpak.agent.vault.query_expansion import tokenize, expand_query, stem_token, get_query_terms_with_weights
except ImportError:
    pass

try:
    from tokenpak.agent.vault.backend_protocol import RetrievalBackend, SemanticScorer
except ImportError:
    pass
