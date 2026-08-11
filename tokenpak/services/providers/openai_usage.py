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
            "prompt_tokens",
            "completion_tokens",
            "input_tokens",
            "output_tokens",
            "total_tokens",
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

    input_tokens = _first_count(usage, "prompt_tokens", "input_tokens")
    completion_tokens = _first_count(usage, "completion_tokens", "output_tokens")
    total_tokens = _count(usage.get("total_tokens"))

    details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    reasoning_tokens = (
        _count(details.get("reasoning_tokens")) if isinstance(details, Mapping) else None
    )

    visible_output_tokens = None
    if completion_tokens is not None and reasoning_tokens is not None:
        visible_output_tokens = max(completion_tokens - reasoning_tokens, 0)

    total_output_tokens = completion_tokens

    total_billable_tokens = None
    if total_tokens is not None:
        total_billable_tokens = total_tokens
    elif input_tokens is not None and total_output_tokens is not None:
        total_billable_tokens = input_tokens + total_output_tokens

    raw_reasoning_effort = usage.get("reasoning_effort")
    reasoning_effort = (
        raw_reasoning_effort
        if isinstance(raw_reasoning_effort, str)
        and raw_reasoning_effort in {"low", "medium", "high"}
        else None
    )

    return {
        "input_tokens": input_tokens,
        "visible_output_tokens": visible_output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_output_tokens": total_output_tokens,
        "total_billable_tokens": total_billable_tokens,
        "reasoning_effort": reasoning_effort,
        "usage_source": "provider_usage_object",
        "provider_usage_ref": _hash_ref(usage),
    }


register_parser(
    PROVIDER_NAME,
    parse_usage,
    input_tokens_include_cache=True,
    cost_policy="catalog_rate_estimate",
)
# Codex subscription traffic uses the same Responses usage contract but may
# carry the router's more specific provider slug.
register_parser(
    "openai-codex",
    parse_usage,
    input_tokens_include_cache=True,
    cost_policy="subscription_billed_unknown",
)
