"""TokenPak proxy cost-tracking middleware.

Provides a hook function designed to be called from any HTTP proxy
after it receives a response from an LLM provider.

Usage (in your proxy's response handler)::

    from tokenpak.proxy.proxy import record_proxy_request

    # After forwarding the upstream response:
    try:
        record_proxy_request(
            model=model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )
    except Exception as e:
        # Graceful degradation: cost tracking failure never crashes the proxy
        print(f"[cost_tracker] warning: {e}")

This is already wired into ~/.tokenpak/proxy.py.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Feature flag — set TOKENPAK_COST_TRACKING=0 to disable
import os as _os

_COST_TRACKING_ENABLED = _os.environ.get("TOKENPAK_COST_TRACKING", "1") != "0"


def record_proxy_request(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    http_status: int = 200,
    session_id: str = "",
) -> float:
    """Record a proxy request in the cost tracker.

    Args:
        http_status: HTTP response status from the upstream provider.
            Non-200 responses (e.g. 429, 401, 403) are logged with cost=0
            because no tokens were actually generated.

    Returns:
        Estimated cost in USD, or 0.0 on failure (graceful degradation).
    """
    if not _COST_TRACKING_ENABLED:
        return 0.0
    try:
        from tokenpak.telemetry.cost_tracker import get_cost_tracker

        tracker = get_cost_tracker()
        cost = tracker.record_request(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            http_status=http_status,
            session_id=session_id,
        )
    except Exception as exc:
        # Emit a structured ERROR so failed tracking attempts leave an audit trail.
        # WARNING level was insufficient — ops needs to know cost data is missing.
        logger.error(
            "COST_TRACKING_FAILURE model=%s tokens=%d error=%s",
            model,
            prompt_tokens + completion_tokens,
            exc,
        )
        cost = 0.0

    return cost
