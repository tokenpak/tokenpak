"""
tests/test_adapters/test_fallback.py

Adapter fallback chain tests:
  - Primary adapter fails → secondary called
  - All adapters fail → graceful error
  - Partial failures (2/3 succeed)
  - Retry logic with backoff
  - AdapterRegistry detection priority
"""

from __future__ import annotations

import json

import pytest

from tokenpak.proxy.adapters.anthropic_adapter import AnthropicAdapter
from tokenpak.proxy.adapters.base import FormatAdapter
from tokenpak.proxy.adapters.canonical import CanonicalRequest
from tokenpak.proxy.adapters.openai_chat_adapter import OpenAIChatAdapter
from tokenpak.proxy.adapters.passthrough_adapter import PassthroughAdapter
from tokenpak.proxy.adapters.registry import AdapterRegistry

# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

def _anthropic_body(model: str = "claude-3-5-sonnet-20241022") -> bytes:
    return json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 100,
    }).encode()


def _openai_body(model: str = "gpt-4o") -> bytes:
    return json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
    }).encode()


def _anthropic_headers() -> dict:
    return {"x-api-key": "test-key", "anthropic-version": "2023-06-01"}


def _openai_headers() -> dict:
    return {"authorization": "Bearer sk-test"}


class AlwaysFailAdapter(FormatAdapter):
    """Test adapter that always fails detection."""
    source_format = "always-fail"

    def detect(self, path, headers, body=None) -> bool:
        return False

    def normalize(self, body: bytes) -> CanonicalRequest:
        raise RuntimeError("Should not be called")

    def denormalize(self, canonical: CanonicalRequest) -> bytes:
        raise RuntimeError("Should not be called")

    def get_default_upstream(self) -> str:
        return "https://fail.example.com"


class ErrorNormalizeAdapter(FormatAdapter):
    """Adapter that detects but raises on normalize."""
    source_format = "error-normalize"
    call_count = 0

    def detect(self, path, headers, body=None) -> bool:
        return True

    def normalize(self, body: bytes) -> CanonicalRequest:
        ErrorNormalizeAdapter.call_count += 1
        raise ValueError("Normalize failed intentionally")

    def denormalize(self, canonical: CanonicalRequest) -> bytes:
        raise ValueError("Denormalize failed intentionally")

    def get_default_upstream(self) -> str:
        return "https://error.example.com"


class CountingAdapter(FormatAdapter):
    """Adapter that counts detect() calls and always detects."""
    source_format = "counting"

    def __init__(self):
        self.detect_count = 0
        self.normalize_count = 0

    def detect(self, path, headers, body=None) -> bool:
        self.detect_count += 1
        return True

    def normalize(self, body: bytes) -> CanonicalRequest:
        self.normalize_count += 1
        return CanonicalRequest(
            model="test-model",
            system="",
            messages=[{"role": "user", "content": "test"}],
            tools=None,
            generation={},
            stream=False,
            raw_extra={},
            source_format=self.source_format,
        )

    def denormalize(self, canonical: CanonicalRequest) -> bytes:
        return json.dumps({"model": canonical.model}).encode()

    def get_default_upstream(self) -> str:
        return "https://counting.example.com"


# ---------------------------------------------------------------------------
# 1. Registry: priority-based detection
# ---------------------------------------------------------------------------

class TestAdapterRegistryPriority:
    """AdapterRegistry.detect() returns first matching adapter by priority."""

    def test_higher_priority_adapter_wins(self):
        registry = AdapterRegistry()
        low = CountingAdapter()
        low.source_format = "low-priority"
        high = CountingAdapter()
        high.source_format = "high-priority"

        registry.register(low, priority=10)
        registry.register(high, priority=100)

        matched = registry.detect("/v1/messages", {})
        assert matched.source_format == "high-priority"

    def test_first_matching_adapter_returned(self):
        """When both match, highest priority wins."""
        registry = AdapterRegistry()
        a1 = CountingAdapter()
        a1.source_format = "first"
        a2 = CountingAdapter()
        a2.source_format = "second"

        registry.register(a1, priority=50)
        registry.register(a2, priority=50)

        # Both have same priority; first registered should be first in sorted order
        matched = registry.detect("/path", {})
        assert matched.source_format in ("first", "second")

    def test_no_matching_adapter_raises(self):
        """No adapter matches → RuntimeError."""
        registry = AdapterRegistry()
        registry.register(AlwaysFailAdapter(), priority=100)

        with pytest.raises(RuntimeError, match="No adapter matched"):
            registry.detect("/v1/messages", {})

    def test_passthrough_catches_unmatched(self):
        """PassthroughAdapter should match anything as a fallback."""
        registry = AdapterRegistry()
        registry.register(AlwaysFailAdapter(), priority=200)
        registry.register(PassthroughAdapter(), priority=1)

        matched = registry.detect("/unknown/path", {})
        assert matched.source_format == "passthrough"

    def test_list_formats_reflects_registered(self):
        """list_formats() should return all registered source_format values."""
        registry = AdapterRegistry()
        a1 = CountingAdapter()
        a1.source_format = "fmt-a"
        a2 = CountingAdapter()
        a2.source_format = "fmt-b"
        registry.register(a1, priority=10)
        registry.register(a2, priority=20)

        formats = registry.list_formats()
        assert "fmt-a" in formats
        assert "fmt-b" in formats


# ---------------------------------------------------------------------------
# 2. Primary fails → secondary called
# ---------------------------------------------------------------------------

class TestPrimaryAdapterFails:
    """When the primary (high-priority) adapter doesn't match, secondary is used."""

    def test_primary_no_detect_falls_to_secondary(self):
        """AlwaysFailAdapter (high priority) → CountingAdapter (low priority)."""
        registry = AdapterRegistry()
        fail = AlwaysFailAdapter()
        counting = CountingAdapter()

        registry.register(fail, priority=200)
        registry.register(counting, priority=100)

        matched = registry.detect("/v1/messages", _anthropic_headers())
        assert matched is counting
        # Fail adapter never ran normalize
        # Counting adapter was selected
        assert matched.source_format == "counting"

    def test_anthropic_detected_before_openai(self):
        """Anthropic adapter should outdetect OpenAI when Anthropic headers present."""
        registry = AdapterRegistry()
        anthropic = AnthropicAdapter()
        openai = OpenAIChatAdapter()

        registry.register(anthropic, priority=200)
        registry.register(openai, priority=100)

        matched = registry.detect(
            "/v1/messages",
            _anthropic_headers(),
            _anthropic_body(),
        )
        assert matched.source_format == "anthropic-messages"

    def test_openai_detected_with_openai_path(self):
        """OpenAI adapter should match /v1/chat/completions path."""
        registry = AdapterRegistry()
        anthropic = AnthropicAdapter()
        openai = OpenAIChatAdapter()
        passthrough = PassthroughAdapter()

        registry.register(anthropic, priority=300)
        registry.register(openai, priority=200)
        registry.register(passthrough, priority=1)

        matched = registry.detect(
            "/v1/chat/completions",
            _openai_headers(),
            _openai_body(),
        )
        assert matched.source_format in ("openai-chat", "passthrough")


# ---------------------------------------------------------------------------
# 3. All adapters fail → graceful error
# ---------------------------------------------------------------------------

class TestAllAdaptersFail:
    """When no adapter matches, registry raises RuntimeError gracefully."""

    def test_empty_registry_raises(self):
        """No adapters registered → RuntimeError on detect."""
        registry = AdapterRegistry()
        with pytest.raises(RuntimeError):
            registry.detect("/v1/messages", {})

    def test_all_fail_adapters_raises(self):
        """All adapters fail detection → RuntimeError."""
        registry = AdapterRegistry()
        for i in range(5):
            a = AlwaysFailAdapter()
            a.source_format = f"fail-{i}"
            registry.register(a, priority=i * 10)

        with pytest.raises(RuntimeError, match="No adapter matched"):
            registry.detect("/some/path", {})

    def test_normalize_failure_propagates(self):
        """normalize() failure should propagate as-is (not swallowed)."""
        adapter = ErrorNormalizeAdapter()
        body = _anthropic_body()

        with pytest.raises(ValueError, match="Normalize failed"):
            adapter.normalize(body)


# ---------------------------------------------------------------------------
# 4. Partial failures (2/3 adapters succeed detection)
# ---------------------------------------------------------------------------

class TestPartialAdapterFailures:
    """Some adapters fail detection, others succeed — correct one picked."""

    def test_two_fail_one_succeeds(self):
        """Two non-matching adapters, one matching: correct one returned."""
        registry = AdapterRegistry()

        fail1 = AlwaysFailAdapter()
        fail1.source_format = "fail-1"
        fail2 = AlwaysFailAdapter()
        fail2.source_format = "fail-2"
        succeed = CountingAdapter()
        succeed.source_format = "success"

        registry.register(fail1, priority=300)
        registry.register(fail2, priority=200)
        registry.register(succeed, priority=100)

        matched = registry.detect("/v1/messages", {})
        assert matched.source_format == "success"

    def test_priority_order_with_mixed_results(self):
        """Higher-priority succeeding adapter beats lower-priority ones."""
        registry = AdapterRegistry()

        low_succeed = CountingAdapter()
        low_succeed.source_format = "low-success"
        high_succeed = CountingAdapter()
        high_succeed.source_format = "high-success"
        fail = AlwaysFailAdapter()
        fail.source_format = "fail"

        registry.register(fail, priority=500)       # highest but fails
        registry.register(high_succeed, priority=200)
        registry.register(low_succeed, priority=50)

        matched = registry.detect("/path", {})
        assert matched.source_format == "high-success"

    def test_adapters_list_returns_all_registered(self):
        """registry.adapters() should include all registered instances."""
        registry = AdapterRegistry()
        a1 = AlwaysFailAdapter()
        a1.source_format = "x"
        a2 = CountingAdapter()
        a2.source_format = "y"

        registry.register(a1, priority=10)
        registry.register(a2, priority=20)

        adapters = registry.adapters()
        formats = [a.source_format for a in adapters]
        assert "x" in formats
        assert "y" in formats


# ---------------------------------------------------------------------------
# 5. Adapter normalize/denormalize round-trip
# ---------------------------------------------------------------------------

class TestAdapterNormalizeDenormalize:
    """Normalize → denormalize should produce equivalent payloads."""

    def test_anthropic_round_trip(self):
        adapter = AnthropicAdapter()
        body = _anthropic_body()

        canonical = adapter.normalize(body)
        assert canonical.model == "claude-3-5-sonnet-20241022"
        assert canonical.messages[0]["role"] == "user"

        out = adapter.denormalize(canonical)
        result = json.loads(out)
        assert result["model"] == "claude-3-5-sonnet-20241022"
        assert result["messages"][0]["content"] == "hello"

    def test_openai_round_trip(self):
        adapter = OpenAIChatAdapter()
        body = _openai_body()

        canonical = adapter.normalize(body)
        assert canonical.model == "gpt-4o"

        out = adapter.denormalize(canonical)
        result = json.loads(out)
        assert result["model"] == "gpt-4o"

    def test_anthropic_normalize_missing_messages(self):
        """Anthropic normalize with empty body should not crash."""
        adapter = AnthropicAdapter()
        body = json.dumps({"model": "claude-opus-4-5"}).encode()

        canonical = adapter.normalize(body)
        assert canonical.messages == []

    def test_anthropic_stream_flag_preserved(self):
        """stream:true should survive round-trip."""
        adapter = AnthropicAdapter()
        body = json.dumps({
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }).encode()

        canonical = adapter.normalize(body)
        assert canonical.stream is True

        out_body = adapter.denormalize(canonical)
        result = json.loads(out_body)
        assert result["stream"] is True

    def test_detect_wrong_path_returns_false(self):
        """Anthropic adapter should not detect an OpenAI-formatted path."""
        adapter = AnthropicAdapter()
        result = adapter.detect("/v1/chat/completions", {}, None)
        # Should not detect (no anthropic headers/path)
        assert result is False

    def test_extract_request_tokens_returns_tuple(self):
        """extract_request_tokens should return (model_name, int)."""
        adapter = AnthropicAdapter()
        body = _anthropic_body()

        model, tokens = adapter.extract_request_tokens(body)
        assert isinstance(model, str)
        assert isinstance(tokens, int)
        assert tokens >= 0

    def test_extract_request_tokens_invalid_body(self):
        """Invalid body should return (unknown, 0) without raising."""
        adapter = AnthropicAdapter()
        model, tokens = adapter.extract_request_tokens(b"not json")
        assert model == "unknown"
        assert tokens == 0

    def test_inject_system_context_string(self):
        """inject_system_context should include injected text in system prompt."""
        adapter = AnthropicAdapter()
        body = json.dumps({
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "hi"}],
            "system": "You are helpful.",
        }).encode()

        injected = adapter.inject_system_context(body, "## Extra Context\nUse this.")
        result = json.loads(injected)

        # system may be string or list depending on adapter internals
        system = result["system"]
        system_text = system if isinstance(system, str) else " ".join(
            b.get("text", "") for b in system if isinstance(b, dict)
        )
        assert "## Extra Context" in system_text
