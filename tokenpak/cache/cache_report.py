# SPDX-License-Identifier: Apache-2.0
"""tokenpak/cache_report.py

Cache report formatting for telemetry trace detail endpoint.
"""

from __future__ import annotations

from typing import Any, Dict


def format_cache_report(
    cache_read_tokens: int = 0,
    new_input_tokens: int = 0,
    turn_id: str = "",
    provider: str = "",
    model: str = "",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Format a cache usage report for a single trace.

    Parameters
    ----------
    cache_read_tokens:
        Number of tokens served from provider cache.
    new_input_tokens:
        Number of new (non-cached) input tokens.
    turn_id:
        Trace or turn identifier.
    provider:
        Provider name.
    model:
        Model identifier.

    Returns
    -------
    dict
        Cache report with hit_tokens, miss_tokens, cache_ratio.
    """
    total = cache_read_tokens + new_input_tokens
    ratio = cache_read_tokens / total if total > 0 else 0.0
    return {
        "turn_id": turn_id,
        "hit_tokens": cache_read_tokens,
        "miss_tokens": new_input_tokens,
        "total_tokens": total,
        "cache_ratio": round(ratio, 4),
        "provider": provider,
        "model": model,
    }
