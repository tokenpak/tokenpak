
import warnings as _warnings
_warnings.warn(
    "tokenpak.agent.vault is deprecated, use tokenpak.vault instead. "
    "This will be removed in v2.0.",
    DeprecationWarning,
    stacklevel=2,
)

# TokenPak vault agent modules

from tokenpak.agent.vault.query_expansion import (
    tokenize,
    expand_query,
    stem_token,
    get_query_terms_with_weights,
    ALIASES,
    STOP_WORDS,
    SUFFIX_RULES,
    WEIGHT_ALIAS,
    WEIGHT_ORIGINAL,
    WEIGHT_STEM,
)

from tokenpak.agent.vault.backend_protocol import (
    RetrievalBackend,
    SemanticScorer,
    RetrievalBackendBase,
    load_custom_backend,
    load_custom_scorer,
)

__all__ = ['tokenize', 'expand_query', 'stem_token', 'get_query_terms_with_weights', 'ALIASES', 'STOP_WORDS', 'SUFFIX_RULES', 'WEIGHT_ALIAS', 'WEIGHT_ORIGINAL', 'WEIGHT_STEM', 'RetrievalBackend', 'SemanticScorer', 'RetrievalBackendBase', 'load_custom_backend', 'load_custom_scorer', 'ast_parser', 'backend_protocol', 'blocks', 'chunk_shaping', 'indexer', 'query_expansion', 'scoring', 'search', 'slicer', 'sqlite_backend', 'symbol_extraction', 'watcher']
