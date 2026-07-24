"""OpenAI reasoning-usage parser.

OpenAI o-series reasoning models surface reasoning usage under
``usage.completion_tokens_details.reasoning_tokens``. Total billable
output is ``completion_tokens`` (which includes reasoning); visible
output is computed as ``completion_tokens - reasoning_tokens``.

Reference fields observed in OpenAI responses as of 2026-05:

    {
        "usage": {
            "prompt_tokens": int,
            "completion_tokens": int,
            "total_tokens": int,
            "completion_tokens_details": {
                "reasoning_tokens": int,
                "audio_tokens": int,        # (not modeled here)
                "accepted_prediction_tokens": int,
                "rejected_prediction_tokens": int
            }
        }
    }

``reasoning_effort`` is set by the caller (low/medium/high). The
provider does not echo it in the usage block as of this writing; if a
future API version starts echoing it, this parser will surface it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from tokenpak.services.providers._registry import register_parser

PROVIDER_NAME = "openai"


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

    input_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")

    details = usage.get("completion_tokens_details") or {}
    reasoning_tokens = details.get("reasoning_tokens") if isinstance(details, dict) else None

    visible_output_tokens = None
    if isinstance(completion_tokens, int) and isinstance(reasoning_tokens, int):
        visible_output_tokens = max(completion_tokens - reasoning_tokens, 0)

    total_output_tokens = completion_tokens if isinstance(completion_tokens, int) else None

    total_billable_tokens = None
    if isinstance(total_tokens, int):
        total_billable_tokens = total_tokens
    elif isinstance(input_tokens, int) and isinstance(total_output_tokens, int):
        total_billable_tokens = input_tokens + total_output_tokens

    reasoning_effort = usage.get("reasoning_effort")
    if reasoning_effort not in {"low", "medium", "high"}:
        reasoning_effort = None

    return {
        "input_tokens": input_tokens if isinstance(input_tokens, int) else None,
        "visible_output_tokens": visible_output_tokens,
        "reasoning_tokens": reasoning_tokens if isinstance(reasoning_tokens, int) else None,
        "total_output_tokens": total_output_tokens,
        "total_billable_tokens": total_billable_tokens,
        "reasoning_effort": reasoning_effort,
        "usage_source": "provider_usage_object",
        "provider_usage_ref": _hash_ref(usage),
    }


register_parser(PROVIDER_NAME, parse_usage)
