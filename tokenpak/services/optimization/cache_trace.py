# SPDX-License-Identifier: Apache-2.0
"""Cache stage trace model.

``CacheStageTrace`` carries the outcome of a single semantic cache lookup
for one optimization context. It is embedded in ``StageTrace.detail`` (as
a JSON string) and also stored as ``OptimizationContext.cache_result`` so
callers in ``proxy/server.py`` can read the hit/miss outcome without parsing the
stage trace log.

Miss-reason vocabulary has a single definition in
``tokenpak.core.contracts.cache.CacheMissReason``.  This module re-exports that
canonical object to preserve the services-layer import path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from tokenpak.core.contracts.cache import CacheMissReason

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
    # Canonical CacheMissReason value when a formal miss applies; otherwise empty.
    miss_reason: str = ""
    strategy: str = "none"  # "exact" | "jaccard" | "none"
    similarity: float = 0.0
    query_hash: str = ""  # first 12 chars of SHA-256 of normalized query
    scope_key_prefix: str = ""  # first 8 chars of scope_key (never full session id)

    # Savings
    savings_tokens: int = 0  # estimated input tokens saved on hit

    # Eligibility metadata
    route: str = ""
    allow_response_reuse: bool = False
    semantic_enabled: bool = True

    # Record status (populated after upstream call by record())
    recorded: bool = False

    def to_detail_str(self) -> str:
        """Serialize to a compact JSON string for ``StageTrace.detail``."""
        return json.dumps(
            {
                "hit": self.hit,
                "miss_reason": self.miss_reason,
                "strategy": self.strategy,
                "similarity": round(self.similarity, 4),
                "query_hash": self.query_hash,
                "savings_tokens": self.savings_tokens,
                "route": self.route,
                "allow_response_reuse": self.allow_response_reuse,
                "recorded": self.recorded,
            },
            separators=(",", ":"),
        )


__all__ = ["CacheStageTrace", "CacheMissReason"]
