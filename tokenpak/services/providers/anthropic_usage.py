"""Anthropic reasoning-usage parser.

Anthropic surfaces extended-thinking reasoning usage via the
``usage`` block on responses to ``messages``. Output-token counts may
roll up reasoning tokens depending on API version; cache-creation /
cache-read tokens are surfaced separately and do not belong in the
reasoning-usage object (they live in the cache columns of monitor.db).

Reference fields observed in Anthropic responses as of 2026-05:

    {
        "usage": {
            "input_tokens": int,
            "output_tokens": int,            # may include reasoning per API version
            "cache_creation_input_tokens": int,
            "cache_read_input_tokens": int
        }
    }

When extended-thinking is enabled, response shape evolves; this parser
treats the absence of a reasoning-specific field as
``reasoning_tokens=None`` rather than guessing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from tokenpak.services.providers._registry import register_parser

PROVIDER_NAME = "anthropic"


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _hash_ref(usage: Mapping[str, object]) -> str:
    raw = json.dumps(usage, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def parse_usage(usage: Mapping[str, object] | None) -> dict[str, object]:
    if not isinstance(usage, Mapping) or not any(
        _count(usage.get(field)) is not None
        for field in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "reasoning_tokens",
            "thinking_tokens",
        )
    ):
        return {
            "input_tokens": None,
            "visible_output_tokens": None,
            "reasoning_tokens": None,
            "total_output_tokens": None,
            "total_billable_tokens": None,
            "reasoning_effort": None,
            "usage_source": "unavailable",
            "provider_usage_ref": None,
        }

    input_tokens = _count(usage.get("input_tokens"))
    output_tokens = _count(usage.get("output_tokens"))
    cache_creation_tokens = _count(usage.get("cache_creation_input_tokens"))
    cache_read_tokens = _count(usage.get("cache_read_input_tokens"))
    reasoning_tokens = _count(usage.get("reasoning_tokens"))
    if reasoning_tokens is None:
        reasoning_tokens = _count(usage.get("thinking_tokens"))

    visible_output_tokens = None
    if output_tokens is not None and reasoning_tokens is not None:
        visible_output_tokens = max(output_tokens - reasoning_tokens, 0)

    total_output_tokens = output_tokens
    total_billable_tokens = None
    if input_tokens is not None and total_output_tokens is not None:
        # Anthropic reports cache-read/cache-creation input separately from
        # ordinary input_tokens. All three classes are billable (at different
        # rates), so the unit total must include them without relabeling the
        # provider's raw input_tokens field.
        total_billable_tokens = (
            input_tokens
            + total_output_tokens
            + (cache_creation_tokens or 0)
            + (cache_read_tokens or 0)
        )

    return {
        "input_tokens": input_tokens,
        "visible_output_tokens": visible_output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_output_tokens": total_output_tokens,
        "total_billable_tokens": total_billable_tokens,
        "reasoning_effort": None,
        "usage_source": "provider_usage_object",
        "provider_usage_ref": _hash_ref(usage),
    }


register_parser(
    PROVIDER_NAME,
    parse_usage,
    input_tokens_include_cache=False,
    cost_policy="catalog_rate_estimate",
)
