"""Google Gemini reasoning-usage parser.

Gemini "thinking" models surface usage via ``usageMetadata`` on
GenerateContentResponse. The thinking-token count appears as
``thoughtsTokenCount`` (alongside ``candidatesTokenCount`` for visible
output). The shape evolved through preview phases; this parser treats
missing fields as ``None`` rather than guessing.

Reference fields observed in Gemini responses as of 2026-05:

    {
        "usageMetadata": {
            "promptTokenCount": int,
            "candidatesTokenCount": int,    # visible output
            "totalTokenCount": int,
            "thoughtsTokenCount": int        # thinking, when enabled
        }
    }
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from tokenpak.services.providers._registry import register_parser

PROVIDER_NAME = "google"


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _first_count(usage: Mapping[str, object], *names: str) -> int | None:
    for name in names:
        value = _count(usage.get(name))
        if value is not None:
            return value
    return None


def _hash_ref(usage: Mapping[str, object]) -> str:
    raw = json.dumps(usage, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def parse_usage(usage: Mapping[str, object] | None) -> dict[str, object]:
    if not isinstance(usage, Mapping) or not any(
        _count(usage.get(field)) is not None
        for field in (
            "promptTokenCount",
            "prompt_token_count",
            "candidatesTokenCount",
            "candidates_token_count",
            "thoughtsTokenCount",
            "thoughts_token_count",
            "totalTokenCount",
            "total_token_count",
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

    input_tokens = _first_count(usage, "promptTokenCount", "prompt_token_count")
    visible_output_tokens = _first_count(usage, "candidatesTokenCount", "candidates_token_count")
    reasoning_tokens = _first_count(usage, "thoughtsTokenCount", "thoughts_token_count")
    total_tokens = _first_count(usage, "totalTokenCount", "total_token_count")

    total_output_tokens = None
    if visible_output_tokens is not None and reasoning_tokens is not None:
        total_output_tokens = visible_output_tokens + reasoning_tokens
    elif visible_output_tokens is not None:
        total_output_tokens = visible_output_tokens

    total_billable_tokens = None
    if total_tokens is not None:
        total_billable_tokens = total_tokens
    elif input_tokens is not None and total_output_tokens is not None:
        total_billable_tokens = input_tokens + total_output_tokens

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
    input_tokens_include_cache=True,
    cost_policy="catalog_rate_estimate",
)
