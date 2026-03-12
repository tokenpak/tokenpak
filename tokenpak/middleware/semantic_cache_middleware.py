"""
tokenpak/middleware/semantic_cache_middleware.py

SemanticCacheMiddleware — Proxy pipeline hook for semantic query reuse.

Insert BEFORE the upstream LLM call::

    from tokenpak.middleware.semantic_cache_middleware import SemanticCacheMiddleware
    from tokenpak.cache import SemanticCacheConfig

    scm = SemanticCacheMiddleware(SemanticCacheConfig(enabled=True, scope="session"))

    # In your request handler:
    query_text = extract_query(request)
    lookup = scm.check(query_text, scope_key=session_id)
    if lookup.hit:
        # Return cached response — no LLM call needed
        response = lookup.entry.response
        trace = scm.build_trace(lookup)
        return response, trace

    response = call_llm(request)
    scm.record(query_text, response, scope_key=session_id)
    return response, {}

Scoping
-------
- ``scope="session"``  → one cache per session_id
- ``scope="agent"``    → one cache per agent_id
- ``scope="global"``   → single shared cache (cross-session reuse)
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

from ..cache.semantic_cache import (
    SemanticCache,
    SemanticCacheConfig,
    SemanticCacheLookup,
)

logger = logging.getLogger(__name__)


class SemanticCacheMiddleware:
    """
    Proxy-level middleware wrapping SemanticCache with scope management.

    Maintains a per-scope cache dict so session-scoped and agent-scoped
    isolation works out-of-the-box.
    """

    def __init__(self, config: Optional[SemanticCacheConfig] = None) -> None:
        self._cfg = config or SemanticCacheConfig()
        # scope_key → SemanticCache
        self._caches: Dict[str, SemanticCache] = {}
        if self._cfg.scope == "global":
            # Pre-create the singleton global cache
            self._caches["__global__"] = SemanticCache(self._cfg)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, query: str, scope_key: str = "") -> SemanticCacheLookup:
        """
        Look up *query* in the appropriate scoped cache.

        Args:
            query:     Raw query text (will be normalised internally).
            scope_key: Session ID, agent ID, or any scoping string.
                       Ignored when ``scope="global"``.

        Returns:
            ``SemanticCacheLookup`` — attach to span/trace metadata.
        """
        cache = self._get_or_create_cache(scope_key)
        lookup = cache.lookup(query)

        if lookup.hit:
            logger.info(
                "[SemanticCacheMiddleware] cache HIT scope=%s strategy=%s sim=%.3f",
                self._resolve_scope_key(scope_key),
                lookup.match_strategy,
                lookup.similarity,
            )
        else:
            logger.debug(
                "[SemanticCacheMiddleware] cache MISS scope=%s",
                self._resolve_scope_key(scope_key),
            )

        return lookup

    def record(self, query: str, response: dict, scope_key: str = "") -> None:
        """
        Store *response* for *query* in the appropriate cache.

        Call this AFTER a successful upstream LLM response.
        """
        cache = self._get_or_create_cache(scope_key)
        cache.store(query, response)
        logger.debug(
            "[SemanticCacheMiddleware] stored query scope=%s",
            self._resolve_scope_key(scope_key),
        )

    def build_trace(self, lookup: SemanticCacheLookup) -> dict:
        """
        Build trace/span metadata from a cache lookup result.

        Suitable for attaching to OpenTelemetry spans, custom trace dicts,
        or TokenPak audit records.
        """
        return {
            "semantic_cache": {
                "hit": lookup.hit,
                "strategy": lookup.match_strategy,
                "similarity": round(lookup.similarity, 4),
                "query_hash": lookup.query_hash[:12] if lookup.query_hash else "",
                "matched_hash": lookup.matched_hash[:12] if lookup.matched_hash else "",
                "savings_tokens": lookup.savings_tokens,
            }
        }

    def stats(self, scope_key: str = "") -> dict:
        """Return hit/miss stats for a given scope."""
        key = self._resolve_scope_key(scope_key)
        cache = self._caches.get(key)
        if cache is None:
            return {"hits": 0, "misses": 0, "total": 0, "hit_rate": 0.0, "size": 0}
        return cache.stats()

    def clear(self, scope_key: str = "") -> None:
        """Clear the cache for *scope_key* (or the global cache)."""
        key = self._resolve_scope_key(scope_key)
        if key in self._caches:
            self._caches[key].clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_scope_key(self, scope_key: str) -> str:
        return "__global__" if self._cfg.scope == "global" else (scope_key or "__default__")

    def _get_or_create_cache(self, scope_key: str) -> SemanticCache:
        key = self._resolve_scope_key(scope_key)
        if key not in self._caches:
            self._caches[key] = SemanticCache(self._cfg)
        return self._caches[key]
