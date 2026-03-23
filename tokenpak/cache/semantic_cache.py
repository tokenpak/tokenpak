"""semantic_cache.py - Re-export from tokenpak_pro (Pro feature).

This module is provided for backward compatibility. The actual implementation
is in tokenpak_pro.
"""

from __future__ import annotations

try:
    from tokenpak_pro.middleware.semantic_cache import (
        CacheEntry,
        SemanticSimilarity,
        SemanticCache,
    )

    # Compatibility aliases
    SemanticCacheConfig = dict  # Placeholder for compatibility
    SemanticCacheEntry = CacheEntry
    SemanticCacheLookup = tuple  # Placeholder for compatibility

    __all__ = [
        "CacheEntry",
        "SemanticSimilarity",
        "SemanticCache",
        "SemanticCacheConfig",
        "SemanticCacheEntry",
        "SemanticCacheLookup",
    ]
except ImportError:
    raise ImportError(
        "semantic_cache requires tokenpak-pro. Install with: pip install tokenpak-pro"
    )
