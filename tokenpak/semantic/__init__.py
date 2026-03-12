"""
TokenPak Semantic Translation Module
======================================
Deterministic resolution of user wording variants to canonical
intent/entity keys for consistent routing.

Usage::

    from tokenpak.semantic import SemanticResolver

    resolver = SemanticResolver()
    result = resolver.resolve_intent("how much did i spend last week")
    # ResolveResult(canonical="usage", alias_matched="how much did i spend", confidence=1.0)

    # Preprocess raw text (replaces aliases in-place for downstream slot fill)
    normalized = resolver.normalize_text("token usage for gpt last 7 days")
    # "usage for model last 7 days"
"""
from .loader import SemanticMapLoader, SemanticMapError
from .resolver import SemanticResolver, ResolveResult

__all__ = [
    "SemanticMapLoader",
    "SemanticMapError",
    "SemanticResolver",
    "ResolveResult",
]
