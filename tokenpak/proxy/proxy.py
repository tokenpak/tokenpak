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

This is already wired into ~/.openclaw/workspace/.tokenpak/proxy.py.
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
    session_id: str = "",
) -> float:
    """Record a proxy request in the cost tracker.

    Returns:
        Estimated cost in USD, or 0.0 on failure (graceful degradation).
    """
    if not _COST_TRACKING_ENABLED:
        return 0.0
    try:
        from tokenpak.agent.telemetry.cost_tracker import get_cost_tracker

        tracker = get_cost_tracker()
        return tracker.record_request(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            session_id=session_id,
        )
    except Exception as exc:
        logger.warning("cost_tracker.record_request failed (request unaffected): %s", exc)
        return 0.0
