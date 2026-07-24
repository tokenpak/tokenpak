"""
TokenPak Semantic Translation Module
======================================
Deterministic resolution of user wording variants to canonical
intent/entity keys for consistent routing.

Usage::

    from tokenpak.vault.semantic import SemanticResolver

    resolver = SemanticResolver()
    result = resolver.resolve_intent("how much did i spend last week")
    # ResolveResult(canonical="usage", alias_matched="how much did i spend", confidence=1.0)

    # Preprocess raw text (replaces aliases in-place for downstream slot fill)
    normalized = resolver.normalize_text("token usage for gpt last 7 days")
    # "usage for model last 7 days"
"""

from .loader import SemanticMapError, SemanticMapLoader
from .resolver import ResolveResult, SemanticResolver

__all__ = [
    "SemanticMapLoader",
    "SemanticMapError",
    "SemanticResolver",
    "ResolveResult",
    "loader",
    "resolver",
    "term_card_builder",
    "term_card_resolver",
    "term_resolver",
]


# Term card features (from agent/semantic/)
try:
    from .term_card_builder import enforce_caps, load_cards, save_cards, validate_card
    from .term_card_resolver import TermCardResolver
    from .term_resolver import TermResolution, TermResolver

    __all__ = [
        "SemanticMapLoader",
        "SemanticMapError",
        "SemanticResolver",
        "ResolveResult",
        "load_cards",
        "save_cards",
        "validate_card",
        "enforce_caps",
        "TermCardResolver",
        "TermResolver",
        "TermResolution",
    ]
except ImportError:
    pass

# Additional exports for TermResolver-based API
from .term_resolver import (
    TermResolution,
    TermResolver,
    TermResolverConfig,
    resolve_terms,
)

__all__ += [
    "TermResolution",
    "TermResolver",
    "TermResolverConfig",
    "resolve_terms",
]
