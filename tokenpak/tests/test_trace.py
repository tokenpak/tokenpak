"""
Unit tests for tokenpak.companion.trace module.

Tests cover the public API:
- TokenPakTrace (dataclass + serialization)
- TraceBuilder (fluent builder)
- Header attachment/stripping/reading
- Envelope attachment/stripping/reading
- No-leak guard
"""

import base64
import json

import pytest

from tokenpak.companion.trace import (
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


class TestTokenPakTrace:
    """Tests for TokenPakTrace dataclass and serialization."""

    def test_trace_to_dict(self):
        """trace.to_dict() returns a plain dict with all fields."""
        trace = TokenPakTrace(
            trace_id="test-id-123",
            timestamp="2026-03-27T00:00:00+00:00",
            routing={"provider": "anthropic", "model": "claude-3-haiku"},
            budget={"tier": "economy", "tokens": 4096},
            retrieval={"sources": ["cache"], "coverage": 0.87},
            packing={"kept_turns": 6, "dropped_turns": 2},
            economics={"actual_tokens": 1800, "cost_usd": 0.0012},
        )
        result = trace.to_dict()
        assert isinstance(result, dict)
        assert result["trace_id"] == "test-id-123"
        assert result["routing"]["provider"] == "anthropic"
        assert result["budget"]["tier"] == "economy"

    def test_trace_to_json(self):
        """trace.to_json() returns valid JSON."""
        trace = TokenPakTrace(
            trace_id="test-id-123",
            timestamp="2026-03-27T00:00:00+00:00",
            routing={"provider": "anthropic", "model": "claude-3-haiku"},
            budget={"tier": "economy", "tokens": 4096},
            retrieval={"sources": [], "coverage": 0.0},
            packing={},
            economics={},
        )
        json_str = trace.to_json()
        parsed = json.loads(json_str)
        assert parsed["trace_id"] == "test-id-123"
        assert parsed["routing"]["provider"] == "anthropic"

    def test_trace_to_base64url(self):
        """trace.to_base64url() returns valid base64url without padding."""
        trace = TokenPakTrace(
            trace_id="test-id-123",
            timestamp="2026-03-27T00:00:00+00:00",
            routing={"provider": "anthropic"},
            budget={"tier": "economy"},
            retrieval={},
            packing={},
            economics={},
        )
        encoded = trace.to_base64url()
        assert isinstance(encoded, str)
        assert "=" not in encoded  # no padding
        # Should be decodable
        padding = 4 - len(encoded) % 4
        if padding != 4:
            encoded += "=" * padding
        decoded = base64.urlsafe_b64decode(encoded)
        assert b"test-id-123" in decoded

    def test_trace_from_base64url(self):
        """TokenPakTrace.from_base64url() decodes a valid trace."""
        trace = TokenPakTrace(
            trace_id="test-id-123",
            timestamp="2026-03-27T00:00:00+00:00",
            routing={"provider": "anthropic", "model": "claude-3-haiku"},
            budget={"tier": "economy", "tokens": 4096},
            retrieval={"sources": ["cache"], "coverage": 0.87},
            packing={"kept_turns": 5},
            economics={"actual_tokens": 1000},
        )
        encoded = trace.to_base64url()
        decoded = TokenPakTrace.from_base64url(encoded)
        assert decoded.trace_id == "test-id-123"
        assert decoded.routing["provider"] == "anthropic"
        assert decoded.budget["tokens"] == 4096

    def test_trace_from_dict(self):
        """TokenPakTrace.from_dict() constructs from a plain dict."""
        data = {
            "trace_id": "test-id-123",
            "timestamp": "2026-03-27T00:00:00+00:00",
            "routing": {"provider": "anthropic"},
            "budget": {"tier": "economy"},
            "retrieval": {},
            "packing": {},
            "economics": {},
            "warnings": ["test warning"],
        }
        trace = TokenPakTrace.from_dict(data)
        assert trace.trace_id == "test-id-123"
        assert trace.warnings == ["test warning"]

    def test_trace_with_warnings(self):
        """TokenPakTrace supports warnings field."""
        trace = TokenPakTrace(
            trace_id="test-id-123",
            timestamp="2026-03-27T00:00:00+00:00",
            routing={},
            budget={},
            retrieval={},
            packing={},
            economics={},
            warnings=["warning 1", "warning 2"],
        )
        assert len(trace.warnings) == 2
        assert trace.warnings[0] == "warning 1"


class TestTraceBuilder:
    """Tests for TraceBuilder fluent builder."""

    def test_builder_empty(self):
        """TraceBuilder.build() creates a valid trace with defaults."""
        builder = TraceBuilder()
        trace = builder.build()
        assert trace.trace_id is not None
        assert trace.timestamp is not None
        assert isinstance(trace.routing, dict)

    def test_builder_routing(self):
        """TraceBuilder.routing() sets routing fields."""
        trace = (
            TraceBuilder()
            .routing(provider="anthropic", model="claude-3-haiku", reason="economy_tier")
            .build()
        )
        assert trace.routing["provider"] == "anthropic"
        assert trace.routing["model"] == "claude-3-haiku"
        assert trace.routing["reason"] == "economy_tier"

    def test_builder_routing_with_rule_id(self):
        """TraceBuilder.routing() supports rule_id."""
        trace = (
            TraceBuilder()
            .routing(
                provider="anthropic",
                model="claude-3-haiku",
                reason="budget",
                rule_id="rule_001",
            )
            .build()
        )
        assert trace.routing["rule_id"] == "rule_001"

    def test_builder_budget(self):
        """TraceBuilder.budget() sets budget fields."""
        trace = (
            TraceBuilder().budget(tier="economy", tokens=4096, reasons=["cost_optimise"]).build()
        )
        assert trace.budget["tier"] == "economy"
        assert trace.budget["tokens"] == 4096
        assert "cost_optimise" in trace.budget["reasons"]

    def test_builder_budget_with_trim_applied(self):
        """TraceBuilder.budget() supports trim_applied flag."""
        trace = (
            TraceBuilder()
            .budget(
                tier="economy",
                tokens=4096,
                reasons=["cost"],
                trim_applied=True,
            )
            .build()
        )
        assert trace.budget["trim_applied"] is True

    def test_builder_retrieval(self):
        """TraceBuilder.retrieval() sets retrieval fields."""
        trace = (
            TraceBuilder()
            .retrieval(sources=["semantic_cache"], top_k=5, coverage=0.87, cache_hit=True)
            .build()
        )
        assert trace.retrieval["sources"] == ["semantic_cache"]
        assert trace.retrieval["top_k"] == 5
        assert trace.retrieval["coverage"] == 0.87
        assert trace.retrieval["cache_hit"] is True

    def test_builder_retrieval_with_retrieval_ms(self):
        """TraceBuilder.retrieval() supports retrieval_ms."""
        trace = (
            TraceBuilder()
            .retrieval(
                sources=["vault"], top_k=10, coverage=0.92, cache_hit=False, retrieval_ms=45.5
            )
            .build()
        )
        assert trace.retrieval["retrieval_ms"] == 45.5

    def test_builder_packing(self):
        """TraceBuilder.packing() sets packing fields."""
        trace = TraceBuilder().packing(kept_turns=6, dropped_turns=2, inject_tokens=312).build()
        assert trace.packing["kept_turns"] == 6
        assert trace.packing["dropped_turns"] == 2
        assert trace.packing["inject_tokens"] == 312

    def test_builder_packing_with_compression_ratio(self):
        """TraceBuilder.packing() supports compression_ratio."""
        trace = (
            TraceBuilder()
            .packing(
                kept_turns=6,
                dropped_turns=2,
                inject_tokens=312,
                compression_ratio=0.75,
            )
            .build()
        )
        assert trace.packing["compression_ratio"] == 0.75

    def test_builder_economics(self):
        """TraceBuilder.economics() sets economics fields."""
        trace = (
            TraceBuilder()
            .economics(actual_tokens=1800, cost_usd=0.0012, savings_usd=0.0038)
            .build()
        )
        assert trace.economics["actual_tokens"] == 1800
        assert trace.economics["cost_usd"] == 0.0012
        assert trace.economics["savings_usd"] == 0.0038

    def test_builder_economics_auto_savings(self):
        """TraceBuilder.economics() auto-computes savings if baseline provided."""
        trace = (
            TraceBuilder()
            .economics(
                actual_tokens=1800,
                cost_usd=0.0012,
                baseline_cost_usd=0.005,
            )
            .build()
        )
        # savings should be baseline - actual = 0.005 - 0.0012 = 0.0038
        assert abs(trace.economics["savings_usd"] - 0.0038) < 0.0001

    def test_builder_warn(self):
        """TraceBuilder.warn() appends warnings."""
        trace = TraceBuilder().warn("warning 1").warn("warning 2").build()
        assert len(trace.warnings) == 2
        assert "warning 1" in trace.warnings

    def test_builder_chaining(self):
        """TraceBuilder supports fluent chaining."""
        trace = (
            TraceBuilder()
            .routing("anthropic", "claude-3-haiku", "economy")
            .budget("economy", 4096, ["cost"])
            .retrieval(["cache"], top_k=5, coverage=0.87)
            .packing(kept_turns=6, dropped_turns=2)
            .economics(actual_tokens=1800, cost_usd=0.0012)
            .warn("test warning")
            .build()
        )
        assert trace.routing["provider"] == "anthropic"
        assert trace.budget["tokens"] == 4096
        assert trace.retrieval["coverage"] == 0.87
        assert len(trace.warnings) == 1


class TestHeaderAttachment:
    """Tests for HTTP header attachment/stripping/reading."""

    def test_attach_trace_header(self):
        """attach_trace_header() adds trace to headers dict."""
        headers = {"Content-Type": "application/json"}
        trace = TraceBuilder().routing("anthropic", "claude-3-haiku").build()
        result = attach_trace_header(headers, trace)
        assert TRACE_HEADER in result
        assert result["Content-Type"] == "application/json"
        assert headers == {"Content-Type": "application/json"}  # original unchanged

    def test_strip_trace_header(self):
        """strip_trace_header() removes trace from headers."""
        headers = {
            "Content-Type": "application/json",
            TRACE_HEADER: "some-value",
            "X-Other": "value",
        }
        result = strip_trace_header(headers)
        assert TRACE_HEADER not in result
        assert result["Content-Type"] == "application/json"
        assert result["X-Other"] == "value"
        assert headers == {
            "Content-Type": "application/json",
            TRACE_HEADER: "some-value",
            "X-Other": "value",
        }  # original unchanged

    def test_strip_trace_header_case_insensitive(self):
        """strip_trace_header() handles case variations."""
        headers = {
            "content-type": "application/json",
            "x-tokenpak-trace": "some-value",  # lowercase variant
        }
        result = strip_trace_header(headers)
        assert "x-tokenpak-trace" not in result
        assert "content-type" in result

    def test_read_trace_header(self):
        """read_trace_header() extracts and decodes a trace."""
        trace = (
            TraceBuilder().routing("anthropic", "claude-3-haiku").budget("economy", 4096).build()
        )
        headers = attach_trace_header({}, trace)
        read_trace = read_trace_header(headers)
        assert read_trace is not None
        assert read_trace.routing["provider"] == "anthropic"
        assert read_trace.budget["tokens"] == 4096

    def test_read_trace_header_missing(self):
        """read_trace_header() returns None if trace absent."""
        headers = {"Content-Type": "application/json"}
        result = read_trace_header(headers)
        assert result is None

    def test_read_trace_header_malformed(self):
        """read_trace_header() returns None for malformed base64url."""
        headers = {TRACE_HEADER: "not-valid-base64!@#$%"}
        result = read_trace_header(headers)
        assert result is None


class TestEnvelopeAttachment:
    """Tests for JSON envelope attachment/stripping/reading."""

    def test_attach_trace_envelope(self):
        """attach_trace_envelope() adds trace to response dict."""
        response = {"choices": [{"message": {"content": "Hello"}}]}
        trace = TraceBuilder().routing("anthropic", "claude-3-haiku").build()
        result = attach_trace_envelope(response, trace)
        assert TRACE_ENVELOPE_KEY in result
        assert result["choices"][0]["message"]["content"] == "Hello"
        assert TRACE_ENVELOPE_KEY not in response  # original unchanged

    def test_strip_trace(self):
        """strip_trace() removes trace from response dict."""
        response = {
            "choices": [{"message": {"content": "Hello"}}],
            TRACE_ENVELOPE_KEY: {"trace_id": "123"},
            "other_field": "value",
        }
        result = strip_trace(response)
        assert TRACE_ENVELOPE_KEY not in result
        assert result["choices"][0]["message"]["content"] == "Hello"
        assert result["other_field"] == "value"
        assert TRACE_ENVELOPE_KEY in response  # original unchanged

    def test_strip_trace_absent(self):
        """strip_trace() works even if trace is absent."""
        response = {"choices": [{"message": {"content": "Hello"}}]}
        result = strip_trace(response)
        assert TRACE_ENVELOPE_KEY not in result
        assert result["choices"][0]["message"]["content"] == "Hello"

    def test_read_trace_envelope(self):
        """read_trace_envelope() extracts a trace from response."""
        trace = (
            TraceBuilder().routing("anthropic", "claude-3-haiku").budget("economy", 4096).build()
        )
        response = {"choices": [{"message": {"content": "Hello"}}]}
        response_with_trace = attach_trace_envelope(response, trace)
        read_trace = read_trace_envelope(response_with_trace)
        assert read_trace is not None
        assert read_trace.routing["provider"] == "anthropic"

    def test_read_trace_envelope_missing(self):
        """read_trace_envelope() returns None if trace absent."""
        response = {"choices": [{"message": {"content": "Hello"}}]}
        result = read_trace_envelope(response)
        assert result is None

    def test_read_trace_envelope_malformed(self):
        """read_trace_envelope() returns None for malformed trace data."""
        response = {TRACE_ENVELOPE_KEY: {"invalid": "data"}}
        result = read_trace_envelope(response)
        # Missing required fields should raise during from_dict, caught and returned None
        assert result is None


class TestNoLeakGuard:
    """Tests for assert_no_leak() guard function."""

    def test_assert_no_leak_envelope_present(self):
        """assert_no_leak() raises if trace envelope is in response."""
        response = {
            "choices": [{"message": {"content": "Hello"}}],
            TRACE_ENVELOPE_KEY: {"trace_id": "123"},
        }
        with pytest.raises(AssertionError, match="Trace envelope key"):
            assert_no_leak(response)

    def test_assert_no_leak_openai_style_content(self):
        """assert_no_leak() checks OpenAI-style content."""
        response = {"choices": [{"message": {"content": f"Hello {TRACE_HEADER} world"}}]}
        with pytest.raises(AssertionError, match="Trace marker found"):
            assert_no_leak(response)

    def test_assert_no_leak_anthropic_style_content(self):
        """assert_no_leak() checks Anthropic-style content."""
        response = {"content": [{"type": "text", "text": f"Hello {TRACE_ENVELOPE_KEY} world"}]}
        with pytest.raises(AssertionError, match="Trace marker found"):
            assert_no_leak(response)

    def test_assert_no_leak_clean_response(self):
        """assert_no_leak() passes for clean response."""
        response = {
            "choices": [{"message": {"content": "Hello world"}}],
            "content": [{"type": "text", "text": "This is safe"}],
        }
        # Should not raise
        assert_no_leak(response)

    def test_assert_no_leak_empty_response(self):
        """assert_no_leak() passes for empty response."""
        response = {}
        # Should not raise
        assert_no_leak(response)

    def test_assert_no_leak_content_array_openai(self):
        """assert_no_leak() checks content arrays in OpenAI-style."""
        response = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "Safe text"},
                            {"type": "text", "text": f"Unsafe {TRACE_HEADER}"},
                        ]
                    }
                }
            ]
        }
        with pytest.raises(AssertionError, match="Trace marker found"):
            assert_no_leak(response)


class TestRoundTrip:
    """Integration tests for round-trip serialization."""

    def test_header_roundtrip(self):
        """Headers can be encoded and decoded without loss."""
        original = (
            TraceBuilder()
            .routing("anthropic", "claude-3-haiku", "economy", rule_id="r1")
            .budget("economy", 4096, ["cost", "speed"], trim_applied=True)
            .retrieval(["cache", "vault"], 5, 0.92, True, retrieval_ms=123.45)
            .packing(6, 2, 312, compression_ratio=0.75)
            .economics(1800, 0.0012, 0.0038, baseline_tokens=2000, baseline_cost_usd=0.005)
            .warn("test")
            .build()
        )
        headers = attach_trace_header({}, original)
        recovered = read_trace_header(headers)
        assert recovered.trace_id == original.trace_id
        assert recovered.routing["provider"] == "anthropic"
        assert recovered.budget["trim_applied"] is True
        assert recovered.retrieval["retrieval_ms"] == 123.45

    def test_envelope_roundtrip(self):
        """Envelopes can be encoded and decoded without loss."""
        original = (
            TraceBuilder().routing("openai", "gpt-4", "quality").budget("unlimited", 8000).build()
        )
        response = attach_trace_envelope({}, original)
        recovered = read_trace_envelope(response)
        assert recovered.routing["provider"] == "openai"
        assert recovered.routing["model"] == "gpt-4"
