# SPDX-License-Identifier: Apache-2.0
"""Cache stage trace model (TIP-04).

``CacheStageTrace`` carries the outcome of a single semantic cache lookup
for one optimization context. It is embedded in ``StageTrace.detail`` (as
a JSON string) and also stored as ``OptimizationContext.cache_result`` so
callers in ``proxy/server.py`` can read the hit/miss outcome without parsing the
stage trace log.

Miss reason vocabulary mirrors ``tokenpak.tip.cache_contract.CacheMissReason``
(copied inline for import-time safety when TIP-02 is not yet on this host).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Miss-reason vocabulary (mirrors CacheMissReason from TIP-02 cache_contract)
# ---------------------------------------------------------------------------

class CacheMissReason:
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
    FLAG_OFF = "flag_off"
    NO_QUERY_TEXT = "no_query_text"


# ---------------------------------------------------------------------------
# Trace dataclass
# ---------------------------------------------------------------------------

@dataclass
class CacheStageTrace:
    """Outcome of one semantic cache lookup/record cycle.

    Attached to ``OptimizationContext.cache_result`` by ``SemanticCacheStage``.
    Never stores raw prompt text — only hashed/normalized values.
    """

    # Lookup outcome
    hit: bool = False
    miss_reason: str = ""          # one of CacheMissReason.*; empty on hit
    strategy: str = "none"         # "exact" | "jaccard" | "none"
    similarity: float = 0.0
    query_hash: str = ""           # first 12 chars of SHA-256 of normalized query
    scope_key_prefix: str = ""     # first 8 chars of scope_key (never full session id)

    # Savings
    savings_tokens: int = 0        # estimated input tokens saved on hit

    # Eligibility metadata
    route: str = ""
    allow_response_reuse: bool = False
    semantic_enabled: bool = True

    # Record status (populated after upstream call by record())
    recorded: bool = False

    def to_detail_str(self) -> str:
        """Serialize to a compact JSON string for ``StageTrace.detail``."""
        return json.dumps({
            "hit": self.hit,
            "miss_reason": self.miss_reason,
            "strategy": self.strategy,
            "similarity": round(self.similarity, 4),
            "query_hash": self.query_hash,
            "savings_tokens": self.savings_tokens,
            "route": self.route,
            "allow_response_reuse": self.allow_response_reuse,
            "recorded": self.recorded,
        }, separators=(",", ":"))


__all__ = ["CacheStageTrace", "CacheMissReason"]
