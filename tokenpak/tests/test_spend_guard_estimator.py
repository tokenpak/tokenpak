# SPDX-License-Identifier: Apache-2.0
"""Unit tests for tokenpak.proxy.spend_guard.estimator.

Token + cost projection tests.
"""

from __future__ import annotations

import json

from tokenpak.proxy.spend_guard.estimator import _count_text_tokens, estimate


class TestCountTextTokens:
    def test_empty_returns_zero(self):
        assert _count_text_tokens("") == 0
        assert _count_text_tokens(None) == 0

    def test_short_text_at_least_one(self):
        assert _count_text_tokens("hi") == 1

    def test_long_text_chars_per_4(self):
        text = "a" * 4000
        assert _count_text_tokens(text) == 1000


class TestEstimateAnthropicShape:
    def _body(self, **kw) -> bytes:
        body = {
            "model": "claude-opus-4-7",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": "hello"}],
        }
        body.update(kw)
        return json.dumps(body).encode()

    def test_small_request_low_cost(self):
        r = estimate(self._body(), model="claude-opus-4-7")
        assert r.projected_input_tokens < 100
        assert r.projected_cost_usd < 0.5  # 4096 output @ $75/MTok ≈ $0.31

    def test_large_user_message_classified_fresh(self):
        big = "x" * 4_000_000  # 1M tokens (4 chars/tok)
        r = estimate(
            self._body(messages=[{"role": "user", "content": big}]), model="claude-opus-4-7"
        )
        assert r.request_tokens >= 999_000
        assert r.current_context_tokens == 0
        assert r.projected_input_tokens >= 999_000

    def test_cache_control_marks_cached(self):
        body = json.dumps(
            {
                "model": "claude-opus-4-7",
                "max_tokens": 4096,
                "system": [
                    {"type": "text", "text": "y" * 400_000, "cache_control": {"type": "ephemeral"}},
                ],
                "messages": [{"role": "user", "content": "hi"}],
            }
        ).encode()
        r = estimate(body, model="claude-opus-4-7")
        assert r.current_context_tokens >= 99_000
        assert r.cache_hit_ratio > 0.9

    def test_tools_count_as_cached(self):
        body = json.dumps(
            {
                "model": "claude-opus-4-7",
                "max_tokens": 4096,
                "tools": [{"name": "x", "input_schema": {"type": "object"}} for _ in range(50)],
                "messages": [{"role": "user", "content": "hi"}],
            }
        ).encode()
        r = estimate(body, model="claude-opus-4-7")
        assert r.current_context_tokens > 0


class TestEstimateUnknownModel:
    def test_unknown_model_falls_back(self):
        # Unknown model shouldn't crash — registry returns sonnet-class defaults.
        body = json.dumps(
            {
                "model": "fictional-model-99",
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": "hi"}],
            }
        ).encode()
        r = estimate(body, model="fictional-model-99")
        assert r.rates["input"] > 0
        assert r.rates["output"] > 0
        assert r.projected_cost_usd >= 0


class TestEstimateMalformedBody:
    def test_non_json_body_treated_as_fresh(self):
        body = b"this is not json at all"
        r = estimate(body, model="claude-opus-4-7")
        # Whole body counted as fresh
        assert r.request_tokens > 0
        assert r.current_context_tokens == 0

    def test_empty_body_returns_zero(self):
        r = estimate(b"", model="claude-opus-4-7")
        assert r.request_tokens == 0
        # Output tokens still default-allocated → cost > 0 (Opus output is expensive)
        assert r.projected_output_tokens > 0
