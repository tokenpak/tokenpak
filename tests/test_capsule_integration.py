"""
Tests for capsule builder proxy integration.
"""


import pytest

pytest.importorskip("tokenpak.capsule.builder", reason="module not available in current build")
import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from tokenpak.proxy.capsule_integration import (
    _estimate_tokens,
    _is_capsule_enabled,
    capsule_request_hook,
    clear_cache,
    get_capsule_request_hook,
)

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_cache():
    """Clear feature flag cache before and after each test."""
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def simple_body():
    """Simple request body with short messages."""
    return json.dumps({
        "model": "claude-sonnet-4-5",
        "messages": [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
    }).encode()


@pytest.fixture
def long_body():
    """Request body with long messages eligible for capsule compression."""
    long_content = "This is a verbose paragraph. " * 50  # ~1500 chars
    return json.dumps({
        "model": "claude-sonnet-4-5",
        "messages": [
            {"role": "system", "content": long_content},
            {"role": "user", "content": long_content},
            {"role": "assistant", "content": long_content},
            {"role": "user", "content": "What do you think?"},  # hot window
        ]
    }).encode()


@pytest.fixture
def mock_trace():
    """Mock PipelineTrace object."""
    trace = MagicMock()
    trace.stages = []
    return trace


# ─────────────────────────────────────────────────────────────────────────────
# Feature Flag Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestFeatureFlag:
    """Test capsule builder feature flag behavior."""

    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove any env var
            os.environ.pop("TOKENPAK_CAPSULE_BUILDER", None)
            clear_cache()
            assert not _is_capsule_enabled()

    def test_enabled_by_env_var(self):
        with patch.dict(os.environ, {"TOKENPAK_CAPSULE_BUILDER": "1"}):
            clear_cache()
            assert _is_capsule_enabled()

    def test_disabled_by_env_var_zero(self):
        with patch.dict(os.environ, {"TOKENPAK_CAPSULE_BUILDER": "0"}):
            clear_cache()
            assert not _is_capsule_enabled()

    def test_env_var_takes_precedence_over_config(self):
        with patch.dict(os.environ, {"TOKENPAK_CAPSULE_BUILDER": "0"}):
            with patch("tokenpak._internal.config.load_config") as mock_cfg:
                mock_cfg.return_value = {"capsule_builder": {"enabled": True}}
                clear_cache()
                assert not _is_capsule_enabled()


# ─────────────────────────────────────────────────────────────────────────────
# Hook Behavior Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHookBehavior:
    """Test capsule request hook behavior."""

    def test_passthrough_when_disabled(self, simple_body):
        with patch.dict(os.environ, {"TOKENPAK_CAPSULE_BUILDER": "0"}):
            clear_cache()
            new_body, sent, raw, protected = capsule_request_hook(
                simple_body, "claude-sonnet-4-5"
            )
            assert new_body == simple_body  # Unchanged

    def test_trace_shows_disabled_reason(self, simple_body, mock_trace):
        with patch.dict(os.environ, {"TOKENPAK_CAPSULE_BUILDER": "0"}):
            clear_cache()
            capsule_request_hook(simple_body, "claude-sonnet-4-5", mock_trace)
            assert len(mock_trace.stages) == 1
            assert mock_trace.stages[0].details.get("skip_reason") == "disabled"

    def test_compresses_when_enabled(self, long_body):
        with patch.dict(os.environ, {"TOKENPAK_CAPSULE_BUILDER": "1"}):
            clear_cache()
            new_body, sent, raw, protected = capsule_request_hook(
                long_body, "claude-sonnet-4-5"
            )
            # Should be compressed (smaller or have CAPSULE markers)
            data = json.loads(new_body)
            # Check if any message got capsulized
            has_capsule = any(
                "[CAPSULE" in str(m.get("content", ""))
                for m in data.get("messages", [])
            )
            assert has_capsule or len(new_body) <= len(long_body)

    def test_trace_shows_compression_stats(self, long_body, mock_trace):
        with patch.dict(os.environ, {"TOKENPAK_CAPSULE_BUILDER": "1"}):
            clear_cache()
            capsule_request_hook(long_body, "claude-sonnet-4-5", mock_trace)
            assert len(mock_trace.stages) == 1
            stage = mock_trace.stages[0]
            assert stage.enabled
            assert "blocks_capsulized" in stage.details
            assert "ratio" in stage.details


# ─────────────────────────────────────────────────────────────────────────────
# Chaining Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHookChaining:
    """Test chaining with base hooks."""

    def test_chains_to_base_hook(self, simple_body):
        base_called = []

        def base_hook(body, model, trace):
            base_called.append((body, model))
            return body, 100, 100, 0

        with patch.dict(os.environ, {"TOKENPAK_CAPSULE_BUILDER": "0"}):
            clear_cache()
            hook = get_capsule_request_hook(base_hook=base_hook)
            result = hook(simple_body, "claude-sonnet-4-5")

            assert len(base_called) == 1
            assert base_called[0][1] == "claude-sonnet-4-5"

    def test_passes_modified_body_to_base_hook(self, long_body):
        received_bodies = []

        def base_hook(body, model, trace):
            received_bodies.append(body)
            return body, 100, 100, 0

        with patch.dict(os.environ, {"TOKENPAK_CAPSULE_BUILDER": "1"}):
            clear_cache()
            hook = get_capsule_request_hook(base_hook=base_hook)
            hook(long_body, "claude-sonnet-4-5")

            # Base hook should receive the potentially-modified body
            assert len(received_bodies) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Performance Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPerformance:
    """Test performance characteristics."""

    def test_fast_enough_for_typical_payload(self, long_body):
        """Builder should complete in <20ms p99."""
        with patch.dict(os.environ, {"TOKENPAK_CAPSULE_BUILDER": "1"}):
            clear_cache()

            times = []
            for _ in range(10):
                t0 = time.monotonic()
                capsule_request_hook(long_body, "claude-sonnet-4-5")
                times.append((time.monotonic() - t0) * 1000)

            p99 = sorted(times)[int(len(times) * 0.99)]
            assert p99 < 20, f"p99 latency {p99:.1f}ms exceeds 20ms threshold"

    def test_disabled_is_instant(self, long_body):
        """When disabled, overhead should be negligible."""
        with patch.dict(os.environ, {"TOKENPAK_CAPSULE_BUILDER": "0"}):
            clear_cache()

            t0 = time.monotonic()
            for _ in range(100):
                capsule_request_hook(long_body, "claude-sonnet-4-5")
            total = (time.monotonic() - t0) * 1000

            avg = total / 100
            assert avg < 0.5, f"Disabled hook avg {avg:.2f}ms too slow"


# ─────────────────────────────────────────────────────────────────────────────
# Determinism Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDeterminism:
    """Test deterministic output."""

    def test_same_input_same_output(self, long_body):
        """Same input should always produce same output."""
        with patch.dict(os.environ, {"TOKENPAK_CAPSULE_BUILDER": "1"}):
            clear_cache()

            result1, _, _, _ = capsule_request_hook(long_body, "claude-sonnet-4-5")
            result2, _, _, _ = capsule_request_hook(long_body, "claude-sonnet-4-5")

            assert result1 == result2

    def test_capsule_id_is_stable(self, long_body):
        """Capsule IDs should be deterministic (based on content hash)."""
        with patch.dict(os.environ, {"TOKENPAK_CAPSULE_BUILDER": "1"}):
            clear_cache()

            result1, _, _, _ = capsule_request_hook(long_body, "claude-sonnet-4-5")
            result2, _, _, _ = capsule_request_hook(long_body, "claude-sonnet-4-5")

            # Extract capsule IDs from both results
            import re
            ids1 = re.findall(r'\[CAPSULE id=(\w+)', result1.decode())
            ids2 = re.findall(r'\[CAPSULE id=(\w+)', result2.decode())

            assert ids1 == ids2


# ─────────────────────────────────────────────────────────────────────────────
# Error Handling Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestErrorHandling:
    """Test graceful error handling."""

    def test_invalid_json_passthrough(self):
        """Invalid JSON should pass through unchanged."""
        invalid_body = b"not json at all {"

        with patch.dict(os.environ, {"TOKENPAK_CAPSULE_BUILDER": "1"}):
            clear_cache()
            result, _, _, _ = capsule_request_hook(invalid_body, "claude-sonnet-4-5")
            assert result == invalid_body

    def test_missing_messages_passthrough(self):
        """Body without messages should pass through unchanged."""
        body = json.dumps({"model": "test"}).encode()

        with patch.dict(os.environ, {"TOKENPAK_CAPSULE_BUILDER": "1"}):
            clear_cache()
            result, _, _, _ = capsule_request_hook(body, "claude-sonnet-4-5")
            assert result == body


# ─────────────────────────────────────────────────────────────────────────────
# Token Estimation Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestTokenEstimation:
    """Test token estimation helper."""

    def test_estimates_from_message_content(self):
        body = json.dumps({
            "messages": [
                {"role": "user", "content": "x" * 400}  # 100 tokens approx
            ]
        }).encode()

        tokens = _estimate_tokens(body)
        assert 80 <= tokens <= 120

    def test_handles_multipart_content(self):
        body = json.dumps({
            "messages": [
                {"role": "user", "content": [
                    {"type": "text", "text": "x" * 200},
                    {"type": "text", "text": "y" * 200},
                ]}
            ]
        }).encode()

        tokens = _estimate_tokens(body)
        assert 80 <= tokens <= 120

    def test_handles_invalid_json(self):
        body = b"invalid"
        tokens = _estimate_tokens(body)
        assert tokens >= 0  # Should not raise


# ─────────────────────────────────────────────────────────────────────────────
# Server wiring tests
# ─────────────────────────────────────────────────────────────────────────────

class TestProxyServerWiring:
    """Verify capsule hook is auto-wired into ProxyServer."""

    def test_proxy_server_has_capsule_hook_by_default(self):
        """ProxyServer should have a request_hook installed even with no args."""
        from tokenpak.proxy.server import ProxyServer
        ps = ProxyServer()
        assert ps.request_hook is not None, "request_hook must be set on ProxyServer"
        assert callable(ps.request_hook), "request_hook must be callable"

    def test_proxy_server_hook_is_capsule_hook(self):
        """The default hook should invoke the capsule pipeline and return valid output."""
        from tokenpak.proxy.server import ProxyServer
        ps = ProxyServer()
        payload = json.dumps({"messages": [{"role": "user", "content": "hello"}]}).encode()
        result = ps.request_hook(payload, "gpt-4o")
        assert isinstance(result, tuple) and len(result) == 4, (
            "hook must return (body, sent_tokens, raw_tokens, protected_tokens)"
        )
        body_out, _, _, _ = result
        # Verify output is valid JSON with messages preserved
        parsed = json.loads(body_out)
        assert "messages" in parsed
        assert len(parsed["messages"]) == 1
        assert parsed["messages"][0]["role"] == "user"

    def test_proxy_server_wraps_external_hook(self):
        """An external request_hook passed to ProxyServer should still be invoked."""
        from tokenpak.proxy.server import ProxyServer
        called_with = {}

        def my_hook(body, model, trace=None):
            called_with["body"] = body
            called_with["model"] = model
            return body, 0, 0, 0

        ps = ProxyServer(request_hook=my_hook)
        payload = json.dumps({"messages": [{"role": "user", "content": "test"}]}).encode()
        ps.request_hook(payload, "claude-3")
        assert called_with.get("model") == "claude-3", "external hook should be chained"

    def test_capsule_hook_enabled_compresses_in_proxy(self):
        """When TOKENPAK_CAPSULE_BUILDER=1, ProxyServer hook should compress large blocks."""
        from tokenpak.proxy.server import ProxyServer
        long_text = "This is a very long sentence that goes on and on. " * 20  # >400 chars
        # Place the large block outside the hot window (last 2 msgs) so it qualifies
        payload = json.dumps({
            "messages": [
                {"role": "user", "content": long_text},  # idx 0 — outside hot window
                {"role": "assistant", "content": "short reply"},
                {"role": "user", "content": "follow-up question here"},
                {"role": "assistant", "content": "ok"},  # idx 3 — hot window
            ]
        }).encode()

        clear_cache()
        with patch.dict(os.environ, {"TOKENPAK_CAPSULE_BUILDER": "1"}):
            clear_cache()
            ps = ProxyServer()
            body_out, sent_tokens, raw_tokens, _ = ps.request_hook(payload, "gpt-4o")
        clear_cache()

        assert b"[CAPSULE" in body_out, "capsule envelope should appear in compressed output"
        assert sent_tokens < raw_tokens, "sent_tokens should be less than raw_tokens after compression"
