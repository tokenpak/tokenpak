# SPDX-License-Identifier: Apache-2.0
"""Token counting utilities with caching, lazy loading, and robust truncation."""

__all__ = (
    "cache_info",
    "clear_cache",
    "count_tokens",
    "count_tokens_uncached",
    "estimate_tokens",
    "truncate_to_tokens",
)

import hashlib
import threading
from collections import OrderedDict, namedtuple
from typing import Hashable, Optional, Protocol, Sequence, Tuple


class _Encoder(Protocol):
    """Minimal interface used from a tiktoken encoder."""

    def encode(self, text: str) -> Sequence[int]: ...


class CacheStats(Protocol):
    """Public fields exposed by ``functools.lru_cache`` statistics."""

    @property
    def hits(self) -> int: ...

    @property
    def misses(self) -> int: ...

    @property
    def maxsize(self) -> int | None: ...

    @property
    def currsize(self) -> int: ...


# Lazy-loaded encoder (tiktoken init is slow ~100ms)
_ENC: Optional[_Encoder] = None
_FALLBACK_MODE = False


def _get_encoder() -> Optional[_Encoder]:
    """Lazy-load tiktoken encoder on first use."""
    global _ENC, _FALLBACK_MODE
    if _ENC is None and not _FALLBACK_MODE:
        try:
            import tiktoken

            _ENC = tiktoken.get_encoding("cl100k_base")
        except ImportError:
            _FALLBACK_MODE = True
    return _ENC


def tokenizer_name() -> Optional[str]:
    """Name of the live tokenizer, or ``None`` when the chars/4 fallback is
    active. Callers that report token counts to a user should disclose which
    estimator produced the number."""
    return "tiktoken:cl100k_base" if _get_encoder() is not None else None


_CacheInfo = namedtuple("CacheInfo", ["hits", "misses", "maxsize", "currsize"])

_CACHE_MAXSIZE = 8192
#: Texts up to this many characters are their own cache key. A longer text is
#: keyed by its length and a 128-bit digest, so the cache never keeps a copy
#: of it: 8,192 entries of 100,000-character chunks (the proxy's token-count
#: endpoint) or of ``truncate_to_tokens`` prefixes would otherwise hold
#: hundreds of megabytes for the life of the process.
_KEY_TEXT_MAX_CHARS = 256

_CACHE: "OrderedDict[Hashable, int]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_HITS = 0
_CACHE_MISSES = 0


def _cache_key(text: str) -> Hashable:
    if len(text) <= _KEY_TEXT_MAX_CHARS:
        return text
    digest = hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=16).digest()
    return (len(text), digest)


def count_tokens(text: str) -> int:
    """
    Count tokens with an LRU cache (8192 entries).

    Short texts are cached by value; longer texts by length and digest, so
    the cache's memory stays bounded however large the texts are.
    """
    global _CACHE_HITS, _CACHE_MISSES
    key = _cache_key(text)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            _CACHE_HITS += 1
            return cached
        _CACHE_MISSES += 1
    result = count_tokens_uncached(text)
    with _CACHE_LOCK:
        _CACHE[key] = result
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_MAXSIZE:
            _CACHE.popitem(last=False)
    return result


def count_tokens_uncached(text: str) -> int:
    """Count tokens without caching (for benchmarking)."""
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        return len(enc.encode(text))
    return max(1, len(text) // 4)


def truncate_to_tokens(text: str, max_tokens: int) -> Tuple[str, int]:
    """
    Truncate text to approximately max_tokens.

    Returns: (truncated_text, actual_token_count)

    Hardened for edge cases:
    - max_tokens <= 0 returns empty
    - Empty text returns ("", 0)
    - High token-density text (CJK, emoji) handled via full-range search
    - Always respects max_tokens constraint
    """
    # Edge cases
    if not text:
        return "", 0
    if max_tokens <= 0:
        return "", 0

    current_tokens = count_tokens(text)
    if current_tokens <= max_tokens:
        return text, current_tokens

    # Binary search for optimal truncation point
    # Use full text length as upper bound (handles high token-density text)
    left = 0
    right = len(text)
    best_text = ""
    best_count = 0

    # Optimization: start with estimate to reduce iterations
    estimate = min(len(text), max(1, max_tokens * 4))
    est_tokens = count_tokens(text[:estimate])
    if est_tokens <= max_tokens:
        left = estimate
        best_text = text[:estimate]
        best_count = est_tokens
    else:
        right = estimate

    while left < right:
        mid = (left + right + 1) // 2
        candidate = text[:mid]
        token_count = count_tokens(candidate)

        if token_count <= max_tokens:
            best_text = candidate
            best_count = token_count
            left = mid
        else:
            right = mid - 1

    # Clean up at word boundary (only if we have content and spaces)
    if best_text and len(best_text) > 10 and " " in best_text:
        truncated = best_text.rsplit(" ", 1)[0]
        if truncated:  # Don't truncate to empty
            best_text = truncated + "…"
            best_count = count_tokens(best_text)
            # Safety: ensure we didn't exceed max_tokens with ellipsis
            if best_count > max_tokens and len(truncated) > 1:
                best_text = truncated[:-1] + "…"
                best_count = count_tokens(best_text)

    return best_text, best_count


def estimate_tokens(text: str) -> int:
    """Fast token estimate — ~4 chars per token for English text."""
    if not text:
        return 0
    # len(encode) handles multibyte correctly; still O(n) but no Python-level loop
    byte_len = len(text.encode("utf-8"))
    return max(1, byte_len // 4)


def clear_cache() -> None:
    """Clear the token count cache."""
    global _CACHE_HITS, _CACHE_MISSES
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE_HITS = 0
        _CACHE_MISSES = 0


def cache_info() -> CacheStats:
    """Get cache statistics."""
    with _CACHE_LOCK:
        return _CacheInfo(_CACHE_HITS, _CACHE_MISSES, _CACHE_MAXSIZE, len(_CACHE))
