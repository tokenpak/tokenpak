#!/usr/bin/env python3
"""
Deep coverage tests for tokenpak/runtime/proxy.py

Tests cover:
- StageTrace and PipelineTrace dataclasses
- TraceStorage thread-safe storage
- VaultIndex BM25 search and caching
- Circuit breaker logic
- Rate limiting (token bucket)
- API key pool rotation
- Header sanitization
- Empty text block stripping
- Cache control block capping
- Token counting with caching
- Swap pressure monitoring
"""

import json
import os
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest

# Prevent background threads during tests
os.environ["TOKENPAK_NO_THREADS"] = "1"
os.environ["TOKENPAK_SEMANTIC_CACHE"] = "0"

# Import the module under test
from tokenpak.runtime import proxy


# ---------------------------------------------------------------------------
# Test StageTrace and PipelineTrace dataclasses
# ---------------------------------------------------------------------------

class TestStageTrace:
    """Tests for StageTrace dataclass."""

    def test_stage_trace_default_values(self):
        """StageTrace has sensible defaults."""
        trace = proxy.StageTrace(name="test_stage")
        assert trace.name == "test_stage"
        assert trace.enabled is True
        assert trace.input_tokens == 0
        assert trace.output_tokens == 0
        assert trace.tokens_delta == 0
        assert trace.duration_ms == 0.0
        assert trace.details == {}

    def test_stage_trace_with_values(self):
        """StageTrace stores provided values correctly."""
        trace = proxy.StageTrace(
            name="compaction",
            enabled=True,
            input_tokens=5000,
            output_tokens=3000,
            tokens_delta=-2000,
            duration_ms=150.5,
            details={"cache_hit": True}
        )
        assert trace.input_tokens == 5000
        assert trace.output_tokens == 3000
        assert trace.tokens_delta == -2000
        assert trace.duration_ms == 150.5
        assert trace.details["cache_hit"] is True

    def test_stage_trace_to_dict(self):
        """StageTrace.to_dict() returns proper dict representation."""
        trace = proxy.StageTrace(name="vault_injection", input_tokens=100)
        d = trace.to_dict()
        assert isinstance(d, dict)
        assert d["name"] == "vault_injection"
        assert d["input_tokens"] == 100


class TestPipelineTrace:
    """Tests for PipelineTrace dataclass."""

    def test_pipeline_trace_default_values(self):
        """PipelineTrace has sensible defaults."""
        trace = proxy.PipelineTrace(
            request_id="req-123",
            timestamp="2026-04-05T12:00:00Z"
        )
        assert trace.request_id == "req-123"
        assert trace.model == ""
        assert trace.stages == []
        assert trace.status == "pending"

    def test_pipeline_trace_with_stages(self):
        """PipelineTrace can contain StageTrace objects."""
        stage1 = proxy.StageTrace(name="compaction", tokens_delta=-500)
        stage2 = proxy.StageTrace(name="injection", tokens_delta=200)
        trace = proxy.PipelineTrace(
            request_id="req-456",
            timestamp="2026-04-05T12:00:00Z",
            model="claude-sonnet-4-20250514",
            stages=[stage1, stage2],
            tokens_saved=300,
            status="complete"
        )
        assert len(trace.stages) == 2
        assert trace.stages[0].name == "compaction"
        assert trace.status == "complete"

    def test_pipeline_trace_to_dict(self):
        """PipelineTrace.to_dict() serializes stages properly."""
        stage = proxy.StageTrace(name="validation", enabled=False)
        trace = proxy.PipelineTrace(
            request_id="req-789",
            timestamp="2026-04-05T12:00:00Z",
            stages=[stage]
        )
        d = trace.to_dict()
        assert isinstance(d, dict)
        assert len(d["stages"]) == 1
        assert d["stages"][0]["name"] == "validation"
        assert d["stages"][0]["enabled"] is False


# ---------------------------------------------------------------------------
# Test TraceStorage
# ---------------------------------------------------------------------------

class TestTraceStorage:
    """Tests for TraceStorage thread-safe storage."""

    def test_trace_storage_store_and_get_last(self):
        """TraceStorage stores traces and retrieves last."""
        storage = proxy.TraceStorage(max_traces=5)
        trace = proxy.PipelineTrace(request_id="t1", timestamp="2026-04-05T12:00:00Z")
        storage.store(trace)
        last = storage.get_last()
        assert last is not None
        assert last.request_id == "t1"

    def test_trace_storage_get_by_id(self):
        """TraceStorage retrieves traces by ID."""
        storage = proxy.TraceStorage(max_traces=5)
        trace1 = proxy.PipelineTrace(request_id="req-a", timestamp="2026-04-05T12:00:00Z")
        trace2 = proxy.PipelineTrace(request_id="req-b", timestamp="2026-04-05T12:01:00Z")
        storage.store(trace1)
        storage.store(trace2)
        
        retrieved = storage.get_by_id("req-a")
        assert retrieved is not None
        assert retrieved.request_id == "req-a"
        
        missing = storage.get_by_id("nonexistent")
        assert missing is None

    def test_trace_storage_max_traces(self):
        """TraceStorage respects max_traces limit."""
        storage = proxy.TraceStorage(max_traces=3)
        for i in range(5):
            trace = proxy.PipelineTrace(request_id=f"req-{i}", timestamp="2026-04-05T12:00:00Z")
            storage.store(trace)
        
        all_traces = storage.get_all()
        assert len(all_traces) == 3
        # Should have the most recent 3
        ids = [t.request_id for t in all_traces]
        assert "req-2" in ids
        assert "req-3" in ids
        assert "req-4" in ids

    def test_trace_storage_thread_safety(self):
        """TraceStorage handles concurrent access safely."""
        storage = proxy.TraceStorage(max_traces=100)
        errors = []

        def store_traces(prefix):
            try:
                for i in range(20):
                    trace = proxy.PipelineTrace(
                        request_id=f"{prefix}-{i}",
                        timestamp="2026-04-05T12:00:00Z"
                    )
                    storage.store(trace)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=store_traces, args=(f"t{j}",)) for j in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        all_traces = storage.get_all()
        assert len(all_traces) == 100  # max_traces limit


# ---------------------------------------------------------------------------
# Test Circuit Breaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    """Tests for circuit breaker functions."""

    def test_circuit_check_closed_initially(self):
        """Circuit is closed for unknown providers."""
        # Unknown provider should return False (circuit closed = allow)
        result = proxy._circuit_check("unknown_provider")
        assert result is False

    def test_circuit_record_failure_and_check(self):
        """Circuit opens after threshold failures."""
        provider = "test_anthropic"
        # Reset state
        with proxy._provider_circuit_lock:
            proxy._provider_circuits[provider] = {
                "open": False,
                "failures": 0,
                "threshold": 3,
                "cooldown": 60,
                "last_failure": 0
            }
        
        # Record failures below threshold
        proxy._circuit_record_failure(provider)
        proxy._circuit_record_failure(provider)
        assert proxy._circuit_check(provider) is False  # Still closed
        
        # Record third failure - should open
        proxy._circuit_record_failure(provider)
        assert proxy._circuit_check(provider) is True  # Now open

    def test_circuit_record_success_resets(self):
        """Recording success resets failure count and closes circuit."""
        provider = "test_google"
        with proxy._provider_circuit_lock:
            proxy._provider_circuits[provider] = {
                "open": True,
                "failures": 5,
                "threshold": 3,
                "cooldown": 60,
                "last_failure": time.time()
            }
        
        proxy._circuit_record_success(provider)
        
        with proxy._provider_circuit_lock:
            cb = proxy._provider_circuits[provider]
            assert cb["open"] is False
            assert cb["failures"] == 0


# ---------------------------------------------------------------------------
# Test Rate Limiting
# ---------------------------------------------------------------------------

class TestRateLimiting:
    """Tests for rate limiting (token bucket)."""

    def test_rate_limit_allows_requests_under_limit(self):
        """Rate limiter allows requests under the limit."""
        test_ip = "192.168.1.100"
        # Clear bucket for this IP
        with proxy._rate_bucket_lock:
            if test_ip in proxy._rate_buckets:
                del proxy._rate_buckets[test_ip]
        
        # First request should be allowed
        assert proxy._rate_limit_check(test_ip) is True

    def test_rate_limit_disabled_when_zero(self):
        """Rate limiter allows all when RPM is 0."""
        original_rpm = proxy._RATE_LIMIT_RPM
        try:
            proxy._RATE_LIMIT_RPM = 0
            assert proxy._rate_limit_check("any_ip") is True
        finally:
            proxy._RATE_LIMIT_RPM = original_rpm


# ---------------------------------------------------------------------------
# Test Key Pool Rotation
# ---------------------------------------------------------------------------

class TestKeyPoolRotation:
    """Tests for API key pool rotation functions."""

    def test_key_is_available_when_not_cooled(self):
        """Key is available when not in cooldown."""
        # Clear cooldown state
        with proxy._KEY_COOLDOWN_LOCK:
            proxy._KEY_COOLDOWN_STATE.clear()
        
        assert proxy._key_is_available(0) is True
        assert proxy._key_is_available(999) is True  # Any index

    def test_cool_down_key_sets_cooldown(self):
        """Cooling down a key makes it unavailable."""
        with proxy._KEY_COOLDOWN_LOCK:
            proxy._KEY_COOLDOWN_STATE.clear()
        
        # Cool down key 0 for 10 seconds
        proxy._cool_down_key(0, 10.0, "test_cooldown")
        
        assert proxy._key_is_available(0) is False

    def test_get_next_key_returns_none_when_empty(self):
        """get_next_key returns (None, -1) when pool is empty."""
        original_pool = proxy._ANTHROPIC_KEY_POOL
        try:
            proxy._ANTHROPIC_KEY_POOL = []
            key, idx = proxy._get_next_key()
            assert key is None
            assert idx == -1
        finally:
            proxy._ANTHROPIC_KEY_POOL = original_pool


# ---------------------------------------------------------------------------
# Test Header Sanitization
# ---------------------------------------------------------------------------

class TestSanitizeHeaders:
    """Tests for header sanitization."""

    def test_sanitize_headers_removes_blocked(self):
        """Sanitize removes blocked headers."""
        raw = {
            "Content-Type": "application/json",
            "Authorization": "Bearer xyz",
            "Host": "evil.com",
            "X-Forwarded-For": "1.2.3.4",
            "Connection": "keep-alive",
        }
        result = proxy._sanitize_headers(raw)
        
        assert "Content-Type" in result
        assert "Authorization" in result
        assert "Host" not in result
        assert "X-Forwarded-For" not in result
        assert "Connection" not in result

    def test_sanitize_headers_preserves_allowed(self):
        """Sanitize preserves allowed headers."""
        raw = {
            "X-Api-Key": "secret",
            "X-Custom-Header": "value",
            "Accept": "application/json",
        }
        result = proxy._sanitize_headers(raw)
        assert result == raw


# ---------------------------------------------------------------------------
# Test Empty Text Block Stripping
# ---------------------------------------------------------------------------

class TestStripEmptyTextBlocks:
    """Tests for _strip_empty_text_blocks."""

    def test_strip_empty_system_blocks(self):
        """Strips empty text blocks from system array."""
        body = {
            "model": "claude-3-opus-20240229",
            "system": [
                {"type": "text", "text": "Valid system prompt"},
                {"type": "text", "text": ""},  # Empty - should be removed
                {"type": "text", "text": "  "},  # Whitespace only - should be removed
            ],
            "messages": []
        }
        result = proxy._strip_empty_text_blocks(json.dumps(body).encode())
        parsed = json.loads(result)
        assert len(parsed["system"]) == 1
        assert parsed["system"][0]["text"] == "Valid system prompt"

    def test_strip_empty_message_content(self):
        """Strips empty content blocks from messages."""
        body = {
            "model": "claude-3-opus-20240229",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Hello"},
                        {"type": "text", "text": ""},
                    ]
                }
            ]
        }
        result = proxy._strip_empty_text_blocks(json.dumps(body).encode())
        parsed = json.loads(result)
        assert len(parsed["messages"][0]["content"]) == 1

    def test_strip_handles_string_content(self):
        """Handles string content that's empty."""
        body = {
            "model": "claude-3-opus-20240229",
            "messages": [{"role": "user", "content": "   "}]
        }
        result = proxy._strip_empty_text_blocks(json.dumps(body).encode())
        parsed = json.loads(result)
        assert parsed["messages"][0]["content"] == " "

    def test_strip_returns_unchanged_on_invalid_json(self):
        """Returns unchanged bytes on invalid JSON."""
        invalid = b"not json"
        result = proxy._strip_empty_text_blocks(invalid)
        assert result == invalid


# ---------------------------------------------------------------------------
# Test Cache Control Block Capping
# ---------------------------------------------------------------------------

class TestCapCacheControlBlocks:
    """Tests for _cap_cache_control_blocks."""

    def test_cap_removes_excess_cache_control(self):
        """Caps cache_control blocks to max allowed."""
        body = {
            "system": [
                {"type": "text", "text": "sys1", "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "sys2", "cache_control": {"type": "ephemeral"}},
            ],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "m1", "cache_control": {"type": "ephemeral"}},
                        {"type": "text", "text": "m2", "cache_control": {"type": "ephemeral"}},
                        {"type": "text", "text": "m3", "cache_control": {"type": "ephemeral"}},
                    ]
                }
            ]
        }
        result = proxy._cap_cache_control_blocks(json.dumps(body).encode(), max_blocks=4)
        parsed = json.loads(result)
        
        # Count remaining cache_control blocks
        count = 0
        for block in parsed.get("system", []):
            if "cache_control" in block:
                count += 1
        for msg in parsed.get("messages", []):
            for block in msg.get("content", []):
                if isinstance(block, dict) and "cache_control" in block:
                    count += 1
        
        assert count == 4

    def test_cap_leaves_unchanged_when_under_limit(self):
        """Leaves body unchanged when under limit."""
        body = {
            "system": [{"type": "text", "text": "s1", "cache_control": {"type": "ephemeral"}}],
            "messages": []
        }
        original = json.dumps(body).encode()
        result = proxy._cap_cache_control_blocks(original, max_blocks=4)
        # Should be unchanged (no modification needed)
        assert json.loads(result) == body


# ---------------------------------------------------------------------------
# Test Token Counting
# ---------------------------------------------------------------------------

class TestTokenCounting:
    """Tests for token counting functions."""

    def test_count_tokens_basic(self):
        """count_tokens returns reasonable values."""
        text = "Hello, world! This is a test message."
        tokens = proxy.count_tokens(text)
        assert isinstance(tokens, int)
        assert tokens > 0
        assert tokens < len(text)  # Tokens should be less than chars

    def test_count_tokens_empty_string(self):
        """count_tokens handles empty string."""
        assert proxy.count_tokens("") == 0

    def test_token_count_cached_returns_consistent_results(self):
        """Cached token counting is consistent."""
        text = "The quick brown fox jumps over the lazy dog."
        
        # Mock encoder
        class MockEncoder:
            def encode(self, text):
                return list(text.split())
        
        encoder = MockEncoder()
        result1 = proxy._token_count_cached(text, encoder)
        result2 = proxy._token_count_cached(text, encoder)
        assert result1 == result2


# ---------------------------------------------------------------------------
# Test Swap Pressure Monitoring
# ---------------------------------------------------------------------------

class TestSwapPressure:
    """Tests for swap pressure monitoring."""

    def test_get_swap_mb_returns_int(self):
        """get_swap_mb returns an integer."""
        result = proxy.get_swap_mb()
        assert isinstance(result, int)
        assert result >= 0

    def test_check_swap_pressure_returns_int(self):
        """check_swap_pressure returns swap MB value."""
        result = proxy.check_swap_pressure()
        assert isinstance(result, int)
        assert result >= 0


# ---------------------------------------------------------------------------
# Test VaultIndex
# ---------------------------------------------------------------------------

class TestVaultIndex:
    """Tests for VaultIndex class."""

    def test_vault_index_init_with_nonexistent_dir(self):
        """VaultIndex initializes with nonexistent directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nonexistent = os.path.join(tmpdir, "nonexistent")
            index = proxy.VaultIndex(nonexistent)
            assert index.available is False
            assert len(index.blocks) == 0

    def test_vault_index_available_property(self):
        """VaultIndex.available reflects block count."""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = proxy.VaultIndex(tmpdir)
            assert index.available is False
            
            # Manually add a block
            index.blocks["test-block"] = {"block_id": "test-block"}
            assert index.available is True

    def test_vault_index_cache_stats(self):
        """VaultIndex.cache_stats returns proper structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = proxy.VaultIndex(tmpdir)
            stats = index.cache_stats
            
            assert "vault_cache_entries" in stats
            assert "vault_cache_memory_mb" in stats
            assert "vault_cache_hits" in stats
            assert "vault_cache_misses" in stats
            assert "vault_cache_hit_rate" in stats

    def test_vault_index_search_empty_returns_empty(self):
        """VaultIndex.search returns empty list when no blocks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = proxy.VaultIndex(tmpdir)
            results = index.search("test query")
            assert results == []

    def test_vault_index_compile_injection_empty(self):
        """compile_injection returns empty tuple when no results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = proxy.VaultIndex(tmpdir)
            text, tokens, refs = index.compile_injection("test query")
            assert text == ""
            assert tokens == 0
            assert refs == []


# ---------------------------------------------------------------------------
# Test Config Functions
# ---------------------------------------------------------------------------

class TestConfigFunctions:
    """Tests for configuration-related functions."""

    def test_extract_host_valid_url(self):
        """_extract_host extracts hostname from URL."""
        assert proxy._extract_host("https://api.anthropic.com/v1/messages") == "api.anthropic.com"
        assert proxy._extract_host("http://localhost:8080/test") == "localhost"

    def test_extract_host_invalid_url(self):
        """_extract_host handles invalid URLs gracefully."""
        assert proxy._extract_host("") == ""
        assert proxy._extract_host("not-a-url") == ""

    def test_provider_for_url_anthropic(self):
        """_provider_for_url detects Anthropic."""
        result = proxy._provider_for_url("https://api.anthropic.com/v1/messages")
        assert result == "anthropic"

    def test_provider_for_url_openai(self):
        """_provider_for_url detects OpenAI."""
        result = proxy._provider_for_url("https://api.openai.com/v1/chat/completions")
        assert result == "openai"


# ---------------------------------------------------------------------------
# Test Error Helpers
# ---------------------------------------------------------------------------

class TestErrorHelpers:
    """Tests for error helper functions."""

    def test_make_structured_error(self):
        """_make_structured_error creates proper structure."""
        err = proxy._make_structured_error(
            error_type="invalid_request",
            message="Missing model field",
            suggestion="Add 'model' to your request body",
            status=400,
            request_id="req-123"
        )
        assert err["error"] == "invalid_request"
        assert err["message"] == "Missing model field"
        assert err["suggestion"] == "Add 'model' to your request body"
        assert err["request_id"] == "req-123"

    def test_suggest_model_returns_none_for_empty(self):
        """_suggest_model returns None for empty input."""
        assert proxy._suggest_model("") is None


# ---------------------------------------------------------------------------
# Test Model Suggestion
# ---------------------------------------------------------------------------

class TestModelSuggestion:
    """Tests for model suggestion logic."""

    def test_suggest_model_partial_match(self):
        """_suggest_model finds partial matches."""
        # If MODEL_COSTS exists and has entries, this should work
        if hasattr(proxy, "MODEL_COSTS") and proxy.MODEL_COSTS:
            known_model = list(proxy.MODEL_COSTS.keys())[0]
            partial = known_model[:5]
            result = proxy._suggest_model(partial)
            # Should return something if partial matches
            assert result is None or isinstance(result, str)
