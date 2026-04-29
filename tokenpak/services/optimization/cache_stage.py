# SPDX-License-Identifier: Apache-2.0
"""Semantic cache optimization stage (TIP-04).

``SemanticCacheStage`` wraps ``SemanticCache`` (tokenpak.cache) as a generic
``OptimizationStage`` with route/fidelity policy, session-scoped cache keys,
and miss-reason telemetry.

NOTE ON LAYERING: This module is in ``tokenpak.services.optimization`` (Level 3).
It imports ``SemanticCache`` from ``tokenpak.cache`` (Level 0/1) directly,
without going through ``tokenpak.proxy.middleware.semantic_cache_middleware``
(Level 4), maintaining the downward-only dependency rule from
``01-architecture-standard.md §2``.

NOTE ON WIRE FORMAT: ``SemanticCache.store()`` is CCG-15 compliant — it stores
raw bytes + content_type + wire_format. The stage serializes ``response: dict``
→ JSON bytes on record, and deserializes bytes → dict on hit, keeping the
protocol layer wire-format-agnostic.

Feature flag: ``TOKENPAK_SEMANTIC_CACHE_STAGE`` (default off).

Safety rules enforced here:
- Response reuse is OFF by default for all routes.
- Code tasks (code_edit, debugging, etc.) disable semantic matching entirely.
- Streaming requests are bypassed (streaming_not_supported).
- The stage never stores raw prompt text — only normalized/hashed forms.
- The capability ``tip.cache.semantic.v1`` must be present on the adapter.

Pipeline integration (two-phase):

    Phase A — lookup (before upstream call):
        stage.eligible(ctx)         # cheap policy check
        stage.apply(ctx)            # sets ctx.cache_result; if hit, response available
        if ctx.cache_result.hit:
            return ctx.cache_result.cached_response   # skip upstream

    Phase B — record (after upstream call):
        stage.record(ctx, upstream_response)  # store response for future hits
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .cache_key import extract_query_text, is_streaming, make_scope_key
from .cache_policy import get_cache_policy_for_route, is_cache_stage_enabled
from .cache_trace import CacheMissReason, CacheStageTrace
from .context import OptimizationContext
from .stage import EligibilityResult

_log = logging.getLogger(__name__)

_TIP_CACHE_SEMANTIC_V1 = "tip.cache.semantic.v1"

# ---------------------------------------------------------------------------
# Context extension
# ---------------------------------------------------------------------------

# We attach results to OptimizationContext via a side attribute rather than
# modifying the TIP-03 dataclass definition (additive, non-breaking).
_CACHE_RESULT_ATTR = "_tip04_cache_result"
_CACHED_RESPONSE_ATTR = "_tip04_cached_response"


def _get_cache_result(ctx: OptimizationContext) -> Optional[CacheStageTrace]:
    return getattr(ctx, _CACHE_RESULT_ATTR, None)


def _set_cache_result(ctx: OptimizationContext, result: CacheStageTrace) -> None:
    object.__setattr__(ctx, _CACHE_RESULT_ATTR, result)


def _set_cached_response(ctx: OptimizationContext, response: dict) -> None:
    object.__setattr__(ctx, _CACHED_RESPONSE_ATTR, response)


def get_cached_response(ctx: OptimizationContext) -> Optional[dict]:
    """Return the cached response dict set by ``apply()`` on a hit, or None."""
    return getattr(ctx, _CACHED_RESPONSE_ATTR, None)


# ---------------------------------------------------------------------------
# Main stage
# ---------------------------------------------------------------------------


class SemanticCacheStage:
    """Wraps ``SemanticCache`` as a generic optimization stage.

    Uses ``tokenpak.cache.semantic_cache.SemanticCache`` (Level 0) directly to
    stay within the services/ → cache/ dependency tier. One instance is typically
    created at process startup (or per test) and reused across requests so the
    in-process cache persists.
    """

    name: str = "semantic_cache"
    required_capabilities: frozenset = frozenset({_TIP_CACHE_SEMANTIC_V1})

    def __init__(self, env: Optional[Dict[str, str]] = None) -> None:
        self._env = env
        # scope_key → SemanticCache (session-scoped isolation without the proxy middleware)
        self._caches: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # OptimizationStage protocol
    # ------------------------------------------------------------------

    def eligible(self, ctx: OptimizationContext) -> EligibilityResult:
        """Return whether this request should attempt a semantic cache lookup.

        Cheap check — does NOT touch the cache store.
        """
        # 1. Feature flag
        if not is_cache_stage_enabled(self._env):
            return EligibilityResult(
                eligible=False,
                skip_reason="flag-off",
                detail="TOKENPAK_SEMANTIC_CACHE_STAGE not set",
            )

        # 2. Adapter capability gate
        adapter = ctx.adapter
        caps: frozenset = frozenset()
        if adapter is not None:
            caps = getattr(adapter, "capabilities", frozenset()) or frozenset()
        if _TIP_CACHE_SEMANTIC_V1 not in caps:
            return EligibilityResult(
                eligible=False,
                skip_reason="capability-missing",
                detail=f"adapter does not declare {_TIP_CACHE_SEMANTIC_V1}",
            )

        # 3. Streaming bypass — response reuse is unsafe for streamed responses
        if is_streaming(ctx):
            return EligibilityResult(
                eligible=False,
                skip_reason="streaming-not-supported",
                detail="streaming=true in request body",
            )

        # 4. Route/fidelity policy
        policy = get_cache_policy_for_route(ctx.route)
        if not policy.semantic_enabled:
            return EligibilityResult(
                eligible=False,
                skip_reason="route-not-cacheable",
                detail=f"route '{ctx.route}' has semantic_enabled=False (lossless required)",
            )

        return EligibilityResult(eligible=True)

    def apply(self, ctx: OptimizationContext) -> OptimizationContext:
        """Perform the semantic cache lookup and annotate *ctx*.

        Sets ``ctx._tip04_cache_result`` (a ``CacheStageTrace``).
        On a response-reuse hit, also sets ``ctx._tip04_cached_response``.

        Does NOT raise — all errors are caught and treated as a miss.
        """
        policy = get_cache_policy_for_route(ctx.route)
        scope_key = make_scope_key(ctx)
        query_text = extract_query_text(ctx)

        trace = CacheStageTrace(
            route=ctx.route or "unknown",
            allow_response_reuse=policy.allow_response_reuse,
            semantic_enabled=policy.semantic_enabled,
        )

        if not query_text:
            trace.miss_reason = CacheMissReason.NO_QUERY_TEXT
            _set_cache_result(ctx, trace)
            return ctx

        trace.scope_key_prefix = scope_key[:8]

        try:
            cache = self._get_or_create_cache(scope_key, policy)
            lookup = cache.lookup(query_text, expected_format="json")

            trace.hit = lookup.hit
            trace.strategy = lookup.match_strategy
            trace.similarity = lookup.similarity
            trace.query_hash = (lookup.query_hash or "")[:12]
            trace.savings_tokens = lookup.savings_tokens

            if lookup.hit:
                if policy.allow_response_reuse and lookup.entry is not None:
                    # CCG-15: entry.response is raw bytes — deserialize to dict
                    try:
                        cached_dict = json.loads(lookup.entry.response)
                    except Exception:
                        cached_dict = {}
                    _set_cached_response(ctx, cached_dict)
                    _log.info(
                        "[SemanticCacheStage] response-reuse HIT route=%s sim=%.3f",
                        ctx.route, lookup.similarity,
                    )
                elif not policy.allow_response_reuse:
                    # Context-reuse only: hit in cache but won't skip upstream.
                    trace.miss_reason = "context-reuse-only"
                    _log.debug(
                        "[SemanticCacheStage] context-reuse hit route=%s (response reuse disabled)",
                        ctx.route,
                    )
            else:
                trace.miss_reason = _classify_miss_reason(lookup, policy)
                _log.debug(
                    "[SemanticCacheStage] MISS route=%s reason=%s sim=%.3f",
                    ctx.route, trace.miss_reason, lookup.similarity,
                )

        except Exception as exc:
            trace.hit = False
            trace.miss_reason = "stage-error"
            _log.warning("[SemanticCacheStage] lookup error: %s", exc, exc_info=True)

        _set_cache_result(ctx, trace)
        return ctx

    def record(self, ctx: OptimizationContext, response: dict) -> None:
        """Store *response* in the cache for *ctx*'s query (call after upstream).

        Only records when the route's policy allows semantic caching and no
        raw prompt text is stored — only the normalized query is persisted
        inside SemanticCache.

        CCG-15: response dict is serialized to JSON bytes before storage so
        the cache entry is wire-format-aware.
        """
        policy = get_cache_policy_for_route(ctx.route)
        if not policy.semantic_enabled:
            return

        query_text = extract_query_text(ctx)
        if not query_text:
            return

        scope_key = make_scope_key(ctx)
        try:
            cache = self._get_or_create_cache(scope_key, policy)
            # CCG-15: store raw bytes + content_type + wire_format
            response_bytes = json.dumps(response).encode("utf-8")
            cache.store(query_text, response_bytes, "application/json", "json")
            cache_result = _get_cache_result(ctx)
            if cache_result is not None:
                cache_result.recorded = True
            _log.debug(
                "[SemanticCacheStage] recorded response for route=%s scope_prefix=%s",
                ctx.route, scope_key[:8],
            )
        except Exception as exc:
            _log.warning("[SemanticCacheStage] record error: %s", exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_create_cache(self, scope_key: str, policy: Any) -> Any:
        """Return (or create) a SemanticCache for the given scope_key.

        Uses tokenpak.cache.semantic_cache.SemanticCache (Level 0) directly —
        avoids importing from proxy.middleware (Level 4) which would violate
        the downward-only dependency rule (01-architecture-standard.md §2).
        """
        from tokenpak.cache.semantic_cache import SemanticCache, SemanticCacheConfig

        if scope_key not in self._caches:
            cfg = SemanticCacheConfig(
                enabled=True,
                scope=getattr(policy, "scope", "session"),
                ttl_seconds=getattr(policy, "ttl_seconds", 300),
                similarity_threshold=getattr(policy, "similarity_threshold", 0.96),
            )
            self._caches[scope_key] = SemanticCache(cfg)
        return self._caches[scope_key]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _classify_miss_reason(lookup: Any, policy: Any) -> str:
    """Map a SemanticCacheLookup miss to a CacheMissReason token."""
    sim = getattr(lookup, "similarity", 0.0)
    threshold = getattr(policy, "similarity_threshold", 0.96)
    if sim > 0 and sim < threshold:
        return CacheMissReason.BELOW_SIMILARITY_THRESHOLD
    return CacheMissReason.BELOW_SIMILARITY_THRESHOLD  # first-time miss has sim=0


__all__ = ["SemanticCacheStage", "get_cached_response", "_get_cache_result"]
