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


def _hash_ref(usage: Mapping[str, object]) -> str:
    raw = json.dumps(usage, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def parse_usage(usage: Mapping[str, object] | None) -> dict[str, object]:
    if not isinstance(usage, dict):
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

    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    reasoning_tokens = usage.get("reasoning_tokens")
    if reasoning_tokens is None and isinstance(output_tokens, int):
        thinking = usage.get("thinking_tokens")
        if isinstance(thinking, int):
            reasoning_tokens = thinking

    visible_output_tokens = None
    if isinstance(output_tokens, int) and isinstance(reasoning_tokens, int):
        visible_output_tokens = max(output_tokens - reasoning_tokens, 0)

    total_output_tokens = output_tokens if isinstance(output_tokens, int) else None
    total_billable_tokens = None
    if isinstance(input_tokens, int) and isinstance(total_output_tokens, int):
        total_billable_tokens = input_tokens + total_output_tokens

    return {
        "input_tokens": input_tokens if isinstance(input_tokens, int) else None,
        "visible_output_tokens": visible_output_tokens,
        "reasoning_tokens": reasoning_tokens if isinstance(reasoning_tokens, int) else None,
        "total_output_tokens": total_output_tokens,
        "total_billable_tokens": total_billable_tokens,
        "reasoning_effort": None,
        "usage_source": "provider_usage_object",
        "provider_usage_ref": _hash_ref(usage),
    }


register_parser(PROVIDER_NAME, parse_usage)
