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

    input_tokens = usage.get("promptTokenCount") or usage.get("prompt_token_count")
    visible_output_tokens = usage.get("candidatesTokenCount") or usage.get("candidates_token_count")
    reasoning_tokens = usage.get("thoughtsTokenCount") or usage.get("thoughts_token_count")
    total_tokens = usage.get("totalTokenCount") or usage.get("total_token_count")

    total_output_tokens = None
    if isinstance(visible_output_tokens, int) and isinstance(reasoning_tokens, int):
        total_output_tokens = visible_output_tokens + reasoning_tokens
    elif isinstance(visible_output_tokens, int):
        total_output_tokens = visible_output_tokens

    total_billable_tokens = None
    if isinstance(total_tokens, int):
        total_billable_tokens = total_tokens
    elif isinstance(input_tokens, int) and isinstance(total_output_tokens, int):
        total_billable_tokens = input_tokens + total_output_tokens

    return {
        "input_tokens": input_tokens if isinstance(input_tokens, int) else None,
        "visible_output_tokens": visible_output_tokens
        if isinstance(visible_output_tokens, int)
        else None,
        "reasoning_tokens": reasoning_tokens if isinstance(reasoning_tokens, int) else None,
        "total_output_tokens": total_output_tokens,
        "total_billable_tokens": total_billable_tokens,
        "reasoning_effort": None,
        "usage_source": "provider_usage_object",
        "provider_usage_ref": _hash_ref(usage),
    }


register_parser(PROVIDER_NAME, parse_usage)
