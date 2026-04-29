# SPDX-License-Identifier: Apache-2.0
"""TIP cache contract — per-request cache policy and miss reason vocabulary.

``CachePolicy`` describes how the optimization pipeline should approach
caching for a specific request. ``CacheMissReason`` provides a canonical
vocabulary for recording *why* a cache lookup did not return a hit, enabling
actionable telemetry and recommendations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


CacheScopeType = Literal["request", "session", "agent", "workspace", "global"]


@dataclass
class CachePolicy:
    """Per-request cache behavior contract.

    The proxy optimization pipeline reads this to decide whether to attempt
    semantic cache lookup, what scope to use, and whether response reuse
    (vs. context reuse only) is permitted.

    Safe defaults:
    - ``enabled=True`` — cache is on by default (miss is harmless)
    - ``semantic_enabled=False`` — semantic matching is off until explicitly
      enabled per route class; avoids wrong-response risk for code tasks
    - ``scope="session"`` — isolates cache entries per user session
    - ``allow_response_reuse=False`` — context reuse only; response reuse
      requires explicit route-class allowance
    - ``allow_context_reuse=True`` — repeated retrieved context/capsules
      can be served from cache without re-fetching
    """

    enabled: bool = True
    semantic_enabled: bool = False
    scope: CacheScopeType = "session"
    ttl_seconds: int = 300
    similarity_threshold: float = 0.96
    bypass_reason: Optional[str] = None
    provider_prompt_cache_key: Optional[str] = None
    allow_response_reuse: bool = False
    allow_context_reuse: bool = True

    def is_active(self) -> bool:
        """True when caching is enabled and not explicitly bypassed."""
        return self.enabled and self.bypass_reason is None

    def with_bypass(self, reason: str) -> "CachePolicy":
        """Return a copy of this policy with caching bypassed for *reason*."""
        from dataclasses import replace
        return replace(self, bypass_reason=reason)


class CacheMissReason:
    """Canonical vocabulary for semantic cache miss reasons.

    Used in ``CacheTrace.miss_reason`` and stored in telemetry for
    analysis and recommendations. Values are stable strings — do not
    rename without a schema version bump.
    """

    SEMANTIC_CACHE_DISABLED = "semantic_cache_disabled"
    ADAPTER_MISSING_CAPABILITY = "adapter_missing_capability"
    ROUTE_NOT_CACHEABLE = "route_not_cacheable"
    FIDELITY_LOSSLESS_REQUIRED = "fidelity_lossless_required"
    STREAMING_NOT_SUPPORTED = "streaming_not_supported"
    NO_SCOPE_KEY = "no_scope_key"
    BELOW_SIMILARITY_THRESHOLD = "below_similarity_threshold"
    TTL_EXPIRED = "ttl_expired"
    TOOL_SCHEMA_DIGEST_MISMATCH = "tool_schema_digest_mismatch"
    GENERATION_PARAMS_MISMATCH = "generation_params_mismatch"
    MODEL_FAMILY_MISMATCH = "model_family_mismatch"
    SAFETY_POLICY_BYPASS = "safety_policy_bypass"

    ALL: frozenset[str] = frozenset({
        SEMANTIC_CACHE_DISABLED,
        ADAPTER_MISSING_CAPABILITY,
        ROUTE_NOT_CACHEABLE,
        FIDELITY_LOSSLESS_REQUIRED,
        STREAMING_NOT_SUPPORTED,
        NO_SCOPE_KEY,
        BELOW_SIMILARITY_THRESHOLD,
        TTL_EXPIRED,
        TOOL_SCHEMA_DIGEST_MISMATCH,
        GENERATION_PARAMS_MISMATCH,
        MODEL_FAMILY_MISMATCH,
        SAFETY_POLICY_BYPASS,
    })


__all__ = ["CachePolicy", "CacheScopeType", "CacheMissReason"]
