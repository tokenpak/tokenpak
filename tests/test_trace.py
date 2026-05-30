# SPDX-License-Identifier: Apache-2.0
"""Tests for tokenpak.trace — debug trace side-channel.

Coverage:
  1. Trace schema generated with correct fields
  2. Trace NOT present in assistant content (no-leak guarantee)
  3. Header encoding / decoding round-trip
  4. Trace stripped on channel forward (header + envelope)
  5. Economics savings calculation
  6. assert_no_leak raises on polluted content
  7. TraceBuilder produces valid TokenPakTrace
"""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak.trace", reason="module not available in current build")
import json

import pytest
from tokenpak.trace import (
    TRACE_ENVELOPE_KEY,
    TRACE_HEADER,
    TokenPakTrace,
    TraceBuilder,
    assert_no_leak,
    attach_trace_envelope,
    attach_trace_header,
    read_trace_envelope,
    read_trace_header,
    strip_trace,
    strip_trace_header,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trace(**kwargs) -> TokenPakTrace:
    """Create a fully-populated trace with sensible defaults."""
    b = (
        TraceBuilder()
        .routing("anthropic", "claude-3-haiku-20240307", "economy_tier")
        .budget("economy", 4096, ["cost_optimise"])
        .retrieval(["semantic_cache"], top_k=5, coverage=0.87, cache_hit=True)
        .packing(kept_turns=6, dropped_turns=2, inject_tokens=312)
        .economics(actual_tokens=1800, cost_usd=0.0012, savings_usd=0.0038)
    )
    return b.build()


def _openai_response(content: str = "Hello!") -> dict:
    return {
        "id": "chatcmpl-abc",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


def _anthropic_response(text: str = "Hello!") -> dict:
    return {
        "id": "msg_abc",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
    }


# ---------------------------------------------------------------------------
# Test 1 — Trace schema generated with correct fields
# ---------------------------------------------------------------------------


class TestTraceSchema:
    def test_all_fields_present(self):
        trace = _make_trace()
        assert trace.trace_id
        assert trace.timestamp
        assert isinstance(trace.routing, dict)
        assert isinstance(trace.budget, dict)
        assert isinstance(trace.retrieval, dict)
        assert isinstance(trace.packing, dict)
        assert isinstance(trace.economics, dict)
        assert isinstance(trace.warnings, list)

    def test_routing_fields(self):
        trace = _make_trace()
        assert trace.routing["provider"] == "anthropic"
        assert trace.routing["model"] == "claude-3-haiku-20240307"
        assert trace.routing["reason"] == "economy_tier"

    def test_budget_fields(self):
        trace = _make_trace()
        assert trace.budget["tier"] == "economy"
        assert trace.budget["tokens"] == 4096
        assert "cost_optimise" in trace.budget["reasons"]

    def test_retrieval_fields(self):
        trace = _make_trace()
        assert trace.retrieval["cache_hit"] is True
        assert trace.retrieval["top_k"] == 5
        assert trace.retrieval["coverage"] == pytest.approx(0.87)

    def test_packing_fields(self):
        trace = _make_trace()
        assert trace.packing["kept_turns"] == 6
        assert trace.packing["dropped_turns"] == 2
        assert trace.packing["inject_tokens"] == 312

    def test_economics_fields(self):
        trace = _make_trace()
        assert trace.economics["actual_tokens"] == 1800
        assert trace.economics["cost_usd"] == pytest.approx(0.0012)
        assert trace.economics["savings_usd"] == pytest.approx(0.0038)

    def test_to_dict_serialisable(self):
        trace = _make_trace()
        d = trace.to_dict()
        # Must be JSON-serialisable
        encoded = json.dumps(d)
        decoded = json.loads(encoded)
        assert decoded["trace_id"] == trace.trace_id

    def test_builder_unique_trace_ids(self):
        t1 = _make_trace()
        t2 = _make_trace()
        assert t1.trace_id != t2.trace_id


# ---------------------------------------------------------------------------
# Test 2 — Trace NOT in assistant content (no-leak guarantee)
# ---------------------------------------------------------------------------


class TestNoLeak:
    def test_openai_response_clean(self):
        """Fresh OpenAI response has no trace leakage."""
        response = _openai_response()
        assert_no_leak(response)  # must not raise

    def test_anthropic_response_clean(self):
        response = _anthropic_response()
        assert_no_leak(response)  # must not raise

    def test_trace_not_appended_to_content(self):
        """After attaching trace via envelope, assert_no_leak on stripped
        response must not raise."""
        trace = _make_trace()
        response = _openai_response()
        with_trace = attach_trace_envelope(response, trace)
        # Strip before forwarding
        forwarded = strip_trace(with_trace)
        assert_no_leak(forwarded)

    def test_assert_no_leak_raises_on_envelope_present(self):
        """If strip_trace was NOT called, assert_no_leak must raise."""
        trace = _make_trace()
        response = attach_trace_envelope(_openai_response(), trace)
        with pytest.raises(AssertionError, match=TRACE_ENVELOPE_KEY):
            assert_no_leak(response)

    def test_assert_no_leak_raises_on_poisoned_content(self):
        """Trace marker injected into content must be detected."""
        poisoned = _openai_response(
            content=f"Assistant says: {TRACE_ENVELOPE_KEY} found here"
        )
        with pytest.raises(AssertionError):
            assert_no_leak(poisoned)

    def test_headers_do_not_affect_content(self):
        """Attaching trace header does not modify response body."""
        trace = _make_trace()
        response = _openai_response()
        headers = attach_trace_header({}, trace)
        # Response body unchanged
        assert_no_leak(response)
        assert TRACE_HEADER in headers


# ---------------------------------------------------------------------------
# Test 3 — Header encoding / decoding round-trip
# ---------------------------------------------------------------------------


class TestHeaderRoundTrip:
    def test_base64url_round_trip(self):
        trace = _make_trace()
        encoded = trace.to_base64url()
        decoded = TokenPakTrace.from_base64url(encoded)
        assert decoded.trace_id == trace.trace_id
        assert decoded.routing == trace.routing
        assert decoded.economics == trace.economics

    def test_attach_and_read_header(self):
        trace = _make_trace()
        headers = attach_trace_header({"Content-Type": "application/json"}, trace)
        assert TRACE_HEADER in headers

        recovered = read_trace_header(headers)
        assert recovered is not None
        assert recovered.trace_id == trace.trace_id
        assert recovered.budget == trace.budget

    def test_read_header_case_insensitive(self):
        """Header lookup must be case-insensitive."""
        trace = _make_trace()
        lowered = {"x-tokenpak-trace": trace.to_base64url()}
        recovered = read_trace_header(lowered)
        assert recovered is not None
        assert recovered.trace_id == trace.trace_id

    def test_base64url_no_padding(self):
        """Encoded string must not contain ``=`` padding characters."""
        trace = _make_trace()
        encoded = trace.to_base64url()
        assert "=" not in encoded

    def test_read_header_returns_none_on_missing(self):
        assert read_trace_header({"Content-Type": "application/json"}) is None

    def test_read_header_returns_none_on_malformed(self):
        assert read_trace_header({TRACE_HEADER: "!!!not-base64!!!"}) is None


# ---------------------------------------------------------------------------
# Test 4 — Trace stripped on channel forward
# ---------------------------------------------------------------------------


class TestStripTrace:
    def test_strip_trace_removes_envelope_key(self):
        trace = _make_trace()
        response = attach_trace_envelope(_openai_response(), trace)
        assert TRACE_ENVELOPE_KEY in response
        stripped = strip_trace(response)
        assert TRACE_ENVELOPE_KEY not in stripped

    def test_strip_trace_preserves_other_fields(self):
        trace = _make_trace()
        response = attach_trace_envelope(_openai_response(), trace)
        stripped = strip_trace(response)
        assert "choices" in stripped
        assert stripped["id"] == "chatcmpl-abc"

    def test_strip_trace_idempotent(self):
        response = _openai_response()
        assert strip_trace(response) == response  # no-op when key absent

    def test_strip_trace_header_removes_header(self):
        trace = _make_trace()
        headers = attach_trace_header({"Content-Type": "application/json"}, trace)
        stripped = strip_trace_header(headers)
        assert TRACE_HEADER not in stripped
        assert stripped.get("Content-Type") == "application/json"

    def test_strip_trace_header_case_insensitive(self):
        headers = {"x-tokenpak-trace": "some-value", "Accept": "application/json"}
        stripped = strip_trace_header(headers)
        assert "x-tokenpak-trace" not in stripped
        assert "Accept" in stripped

    def test_strip_trace_header_idempotent(self):
        headers = {"Content-Type": "application/json"}
        assert strip_trace_header(headers) == headers


# ---------------------------------------------------------------------------
# Test 5 — Economics savings calculation
# ---------------------------------------------------------------------------


class TestEconomics:
    def test_explicit_savings(self):
        trace = (
            TraceBuilder()
            .routing("openai", "gpt-4o-mini", "default")
            .budget("standard", 8000)
            .retrieval()
            .packing()
            .economics(actual_tokens=2000, cost_usd=0.002, savings_usd=0.005)
            .build()
        )
        assert trace.economics["savings_usd"] == pytest.approx(0.005)

    def test_auto_computed_savings_from_baseline(self):
        """When savings_usd=0 but baseline_cost_usd provided, savings computed."""
        trace = (
            TraceBuilder()
            .routing("openai", "gpt-4o", "default")
            .budget("premium", 16000)
            .retrieval()
            .packing()
            .economics(
                actual_tokens=3000,
                cost_usd=0.003,
                baseline_cost_usd=0.010,
            )
            .build()
        )
        assert trace.economics["savings_usd"] == pytest.approx(0.007)

    def test_savings_never_negative(self):
        """If actual cost exceeds baseline, savings must be 0, not negative."""
        trace = (
            TraceBuilder()
            .routing("openai", "gpt-4o", "default")
            .budget("premium", 16000)
            .retrieval()
            .packing()
            .economics(
                actual_tokens=5000,
                cost_usd=0.020,  # more expensive than baseline
                baseline_cost_usd=0.010,
            )
            .build()
        )
        assert trace.economics["savings_usd"] == pytest.approx(0.0)

    def test_baseline_tokens_stored(self):
        trace = (
            TraceBuilder()
            .routing("openai", "gpt-4o-mini", "default")
            .budget("standard", 8000)
            .retrieval()
            .packing()
            .economics(
                actual_tokens=1500,
                cost_usd=0.001,
                savings_usd=0.003,
                baseline_tokens=4500,
            )
            .build()
        )
        assert trace.economics["baseline_tokens"] == 4500

    def test_zero_economics(self):
        """Empty economics block produces zeroed fields."""
        trace = TraceBuilder().routing("x", "y", "z").budget("t", 0).retrieval().packing().economics().build()
        assert trace.economics["actual_tokens"] == 0
        assert trace.economics["cost_usd"] == 0.0
        assert trace.economics["savings_usd"] == 0.0


# ---------------------------------------------------------------------------
# Test 6 — Envelope round-trip
# ---------------------------------------------------------------------------


class TestEnvelopeRoundTrip:
    def test_attach_and_read_envelope(self):
        trace = _make_trace()
        response = attach_trace_envelope(_openai_response(), trace)
        recovered = read_trace_envelope(response)
        assert recovered is not None
        assert recovered.trace_id == trace.trace_id
        assert recovered.retrieval == trace.retrieval

    def test_read_envelope_returns_none_on_missing(self):
        assert read_trace_envelope(_openai_response()) is None

    def test_original_response_not_mutated(self):
        """attach_trace_envelope must not mutate the original dict."""
        original = _openai_response()
        original_copy = dict(original)
        attach_trace_envelope(original, _make_trace())
        assert original == original_copy
