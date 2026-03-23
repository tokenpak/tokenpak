"""semantic_cache.py - Re-export from tokenpak_pro (Pro feature).

This module is provided for backward compatibility. The actual implementation
is in tokenpak_pro.
"""

from __future__ import annotations

try:
    from tokenpak_pro.features.cache.semantic_cache import (
        SemanticCacheEntry,
        SemanticCacheLookup,
    )

    __all__ = [
        "SemanticCacheEntry",
        "SemanticCacheLookup",
    ]
except ImportError:
    raise ImportError(
        "semantic_cache requires tokenpak-pro. Install with: pip install tokenpak-pro"
    )
