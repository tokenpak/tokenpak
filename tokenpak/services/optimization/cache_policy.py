# SPDX-License-Identifier: Apache-2.0
"""Per-route-class semantic cache policy.

``get_cache_policy_for_route`` returns a ``CachePolicy`` (from the upstream
contract) that reflects the proposal Component C policy table:

    status_check / configuration_inspection / summarization
        → response reuse allowed, conservative thresholds
    general_chat / research / planning / documentation_generation
        → context reuse only, semantic-safe
    code_* / debugging / test_failure / git_diff_review / log_analysis / shell_command_analysis
        → response reuse OFF, context reuse only (lossless-required routes)
    unknown
        → response reuse OFF (safe default)

The flag ``TOKENPAK_SEMANTIC_CACHE_STAGE`` must be set to a truthy value for
the stage to activate. This module only owns policy derivation; flag checking
lives in ``SemanticCacheStage.eligible()``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from tokenpak.tip.cache_contract import CachePolicy

ENV_FLAG = "TOKENPAK_SEMANTIC_CACHE_STAGE"

_TRUTHY = {"1", "on", "true", "yes", "enabled"}


def is_cache_stage_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True when ``TOKENPAK_SEMANTIC_CACHE_STAGE`` is set to a truthy value."""
    source = env if env is not None else os.environ
    return (source.get(ENV_FLAG, "") or "").strip().lower() in _TRUTHY


# ---------------------------------------------------------------------------
# Route-class → CachePolicy table (from proposal Component C)
# ---------------------------------------------------------------------------

# Routes where response reuse is safe with conservative thresholds.
_RESPONSE_REUSE_ROUTES = frozenset(
    {
        "status_check",
        "configuration_inspection",
        "summarization",
    }
)

# Routes where the proxy should avoid all response reuse but context reuse is ok.
_CONTEXT_REUSE_ONLY_ROUTES = frozenset(
    {
        "general_chat",
        "research",
        "planning",
        "documentation_generation",
    }
)

# Routes that require lossless fidelity — disable semantic response reuse entirely.
_LOSSLESS_ROUTES = frozenset(
    {
        "code_generation",
        "code_edit",
        "code_review",
        "debugging",
        "test_failure",
        "log_analysis",
        "git_diff_review",
        "shell_command_analysis",
    }
)

# Per-route thresholds when response reuse IS allowed.
_RESPONSE_REUSE_THRESHOLDS: dict[str, float] = {
    "status_check": 0.94,
    "configuration_inspection": 0.97,
    "summarization": 0.96,
}


def get_cache_policy_for_route(route: str | None) -> CachePolicy:
    """Return the canonical ``CachePolicy`` for *route*.

    The TIP cache contract is part of TokenPak's in-package interface, so all
    callers receive the same concrete policy type.
    """
    route_key = (route or "unknown").lower()

    allow_response_reuse = route_key in _RESPONSE_REUSE_ROUTES
    similarity_threshold = _RESPONSE_REUSE_THRESHOLDS.get(route_key, 0.96)
    # Lossless routes: disable semantic matching entirely (not just response reuse)
    semantic_enabled = route_key not in _LOSSLESS_ROUTES

    return CachePolicy(
        enabled=True,
        semantic_enabled=semantic_enabled,
        scope="session",
        ttl_seconds=300,
        similarity_threshold=similarity_threshold,
        allow_response_reuse=allow_response_reuse,
        allow_context_reuse=True,
    )
