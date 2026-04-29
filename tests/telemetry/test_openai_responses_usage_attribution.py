"""Tests for OpenAI Responses / Codex usage parsing in attribution stage.

Covers:
- AttributionStage feature flag behavior
- parse_response() on real-shaped OpenAI Responses usage dicts
- Provider/platform cache tokens are NOT credited to TokenPak
- Missing/unknown pricing produces tokens-only, no fake cost
- Anthropic usage detection by response shape
- Empty or malformed response bodies are handled safely
"""

from __future__ import annotations

import json

import pytest

from tokenpak.services.optimization.attribution_stage import (
    AttributionStage,
    get_attributions,
    is_attribution_v2_enabled,
)
from tokenpak.services.optimization.context import OptimizationContext
from tokenpak.services.optimization.trace import OptimizationTrace
from tokenpak.tip.telemetry_contract import SavingsSource


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(request_id: str = "req-test") -> OptimizationContext:
    trace = OptimizationTrace(request_id=request_id, mode="observe")
    return OptimizationContext(
        request_id=request_id,
        raw_body=b'{"model": "gpt-5.5", "input": "hello"}',
        trace=trace,
    )


def _openai_response_body(
    prompt_tokens: int = 100,
    cached_tokens: int = 0,
    completion_tokens: int = 50,
) -> bytes:
    return json.dumps(
        {
            "id": "resp-abc",
            "model": "gpt-5.5",
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "prompt_tokens_details": {"cached_tokens": cached_tokens},
            },
        }
    ).encode()


def _anthropic_response_body(
    input_tokens: int = 100,
    cache_read: int = 0,
    output_tokens: int = 50,
) -> bytes:
    return json.dumps(
        {
            "id": "msg-xyz",
            "model": "claude-opus-4-7",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": 0,
        }
    ).encode()


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


def test_attribution_stage_disabled_by_default():
    stage = AttributionStage(env={})
    ctx = _make_ctx()
    result = stage.eligible(ctx)
    assert result.eligible is False
    assert result.skip_reason == "flag-off"


def test_attribution_stage_enabled_by_flag():
    stage = AttributionStage(env={"TOKENPAK_ATTRIBUTION_V2": "1"})
    ctx = _make_ctx()
    result = stage.eligible(ctx)
    assert result.eligible is True


def test_is_attribution_v2_enabled_false_by_default():
    assert is_attribution_v2_enabled({}) is False


def test_is_attribution_v2_enabled_various_truthy():
    for val in ("1", "on", "true", "yes"):
        assert is_attribution_v2_enabled({"TOKENPAK_ATTRIBUTION_V2": val}) is True


# ---------------------------------------------------------------------------
# parse_response — OpenAI Responses with platform cache
# ---------------------------------------------------------------------------


def test_openai_response_with_cached_tokens_yields_platform_cache():
    stage = AttributionStage(env={"TOKENPAK_ATTRIBUTION_V2": "1"})
    ctx = _make_ctx()
    body = _openai_response_body(prompt_tokens=70, cached_tokens=30, completion_tokens=20)

    attrs = stage.parse_response(ctx, body, platform="openai")
    assert len(attrs) == 1
    assert attrs[0].source == SavingsSource.PLATFORM_CACHE
    assert attrs[0].saved_tokens == 30
    assert attrs[0].credited_to_tokenpak is False


def test_openai_response_no_cache_yields_empty():
    stage = AttributionStage(env={"TOKENPAK_ATTRIBUTION_V2": "1"})
    ctx = _make_ctx()
    body = _openai_response_body(prompt_tokens=100, cached_tokens=0)

    attrs = stage.parse_response(ctx, body, platform="openai")
    assert attrs == []


def test_anthropic_response_with_cache_read_yields_provider_prompt_cache():
    stage = AttributionStage(env={"TOKENPAK_ATTRIBUTION_V2": "1"})
    ctx = _make_ctx()
    body = _anthropic_response_body(input_tokens=80, cache_read=20)

    attrs = stage.parse_response(ctx, body, platform="anthropic")
    assert len(attrs) == 1
    assert attrs[0].source == SavingsSource.PROVIDER_PROMPT_CACHE
    assert attrs[0].saved_tokens == 20
    assert attrs[0].credited_to_tokenpak is False


# ---------------------------------------------------------------------------
# Unknown pricing → no fake cost savings
# ---------------------------------------------------------------------------


def test_openai_no_pricing_no_cost_estimate():
    stage = AttributionStage(env={"TOKENPAK_ATTRIBUTION_V2": "1"})
    ctx = _make_ctx()
    body = _openai_response_body(prompt_tokens=100, cached_tokens=50)

    attrs = stage.parse_response(ctx, body, platform="openai")
    assert attrs[0].cost_available is False
    assert attrs[0].estimated_cost_saved is None


# ---------------------------------------------------------------------------
# ctx annotation
# ---------------------------------------------------------------------------


def test_parse_response_annotates_ctx():
    stage = AttributionStage(env={"TOKENPAK_ATTRIBUTION_V2": "1"})
    ctx = _make_ctx()
    body = _openai_response_body(prompt_tokens=100, cached_tokens=40)

    stage.parse_response(ctx, body, platform="openai")
    annotated = get_attributions(ctx)
    assert len(annotated) == 1
    assert annotated[0].source == SavingsSource.PLATFORM_CACHE


def test_parse_response_ctx_empty_when_no_cache():
    stage = AttributionStage(env={"TOKENPAK_ATTRIBUTION_V2": "1"})
    ctx = _make_ctx()
    body = _openai_response_body(prompt_tokens=100, cached_tokens=0)

    attrs = stage.parse_response(ctx, body, platform="openai")
    assert attrs == []
    assert get_attributions(ctx) == []


# ---------------------------------------------------------------------------
# Robustness — malformed / empty bodies
# ---------------------------------------------------------------------------


def test_parse_response_empty_body():
    stage = AttributionStage(env={"TOKENPAK_ATTRIBUTION_V2": "1"})
    ctx = _make_ctx()
    attrs = stage.parse_response(ctx, b"", platform="openai")
    assert attrs == []


def test_parse_response_non_json_body():
    stage = AttributionStage(env={"TOKENPAK_ATTRIBUTION_V2": "1"})
    ctx = _make_ctx()
    attrs = stage.parse_response(ctx, b"not json at all", platform="openai")
    assert attrs == []


def test_parse_response_flag_off_returns_empty():
    stage = AttributionStage(env={})
    ctx = _make_ctx()
    body = _openai_response_body(cached_tokens=100)
    attrs = stage.parse_response(ctx, body, platform="openai")
    assert attrs == []
    assert get_attributions(ctx) == []


# ---------------------------------------------------------------------------
# Attribution invariant: provider/platform savings never credited to TokenPak
# ---------------------------------------------------------------------------


def test_all_parsed_attributions_from_openai_are_not_tokenpak_managed():
    stage = AttributionStage(env={"TOKENPAK_ATTRIBUTION_V2": "1"})
    ctx = _make_ctx()
    body = _openai_response_body(prompt_tokens=200, cached_tokens=100)

    attrs = stage.parse_response(ctx, body, platform="openai")
    for attr in attrs:
        assert attr.credited_to_tokenpak is False, (
            f"Source {attr.source!r} must not be credited to TokenPak"
        )


def test_all_parsed_attributions_from_anthropic_are_not_tokenpak_managed():
    stage = AttributionStage(env={"TOKENPAK_ATTRIBUTION_V2": "1"})
    ctx = _make_ctx()
    body = _anthropic_response_body(input_tokens=200, cache_read=80)

    attrs = stage.parse_response(ctx, body, platform="anthropic")
    for attr in attrs:
        assert attr.credited_to_tokenpak is False, (
            f"Source {attr.source!r} must not be credited to TokenPak"
        )
