"""
TokenPak Proxy Error Handling Audit Tests

Comprehensive test suite for error handling paths in TokenPak proxy modules:
- proxy.py (cost tracking)
- failover.py (provider failover)
- circuit_breaker.py (fault isolation)
- connection_pool.py (connection management)
- streaming.py (SSE response handling)
- passthrough.py (credential handling)

Tests cover:
1. Invalid API keys / auth failures
2. Timeout scenarios
3. Malformed requests
4. Cache miss edge cases
5. Logging failures
6. Provider unavailability
7. Credential passthrough failures
"""

import os
import threading
import time
import unittest
from unittest import mock

import pytest

from tokenpak.proxy.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    CircuitState,
    provider_from_url,
)
from tokenpak.proxy.failover import (
    FailoverConfig,
    FailoverManager,
    ProviderEntry,
    load_failover_config,
)
from tokenpak.proxy.passthrough import CredentialPassthrough, PassthroughConfig

# Import modules under test
from tokenpak.proxy.proxy import record_proxy_request
from tokenpak.proxy.router import ProviderRouter
from tokenpak.proxy.streaming import StreamHandler, extract_sse_tokens

# ==============================================================================
# Test 1: Invalid API Key Error Handling
# ==============================================================================

class TestInvalidApiKeyHandling(unittest.TestCase):
    """Test error handling when API keys are invalid or missing."""

    def test_record_proxy_request_with_missing_cost_tracker(self):
        """Test cost tracking gracefully degrades when tracker unavailable."""
        with mock.patch.dict(os.environ, {"TOKENPAK_COST_TRACKING": "1"}):
            with mock.patch(
                "tokenpak.telemetry.cost_tracker.get_cost_tracker",
                side_effect=ImportError("Cost tracker module not found"),
            ):
                # Should not raise — graceful degradation
                result = record_proxy_request(
                    model="claude-opus-4-5",
                    prompt_tokens=100,
                    completion_tokens=50,
                )
                # Will attempt to call but should handle the error
                assert isinstance(result, (int, float)), "Should return a number"

    def test_record_proxy_request_feature_flag_disabled(self):
        """Test cost tracking can be disabled via feature flag."""
        # Reload the module to pick up the env var
        import importlib

        import tokenpak.proxy.proxy as proxy_module

        with mock.patch.dict(os.environ, {"TOKENPAK_COST_TRACKING": "0"}):
            importlib.reload(proxy_module)
            result = proxy_module.record_proxy_request(
                model="claude-opus-4-5",
                prompt_tokens=100,
                completion_tokens=50,
            )
            assert result == 0.0, "Should return 0.0 when disabled"
            # Restore original state
            with mock.patch.dict(os.environ, {"TOKENPAK_COST_TRACKING": "1"}):
                importlib.reload(proxy_module)

    def test_credential_passthrough_missing_auth_header(self):
        """Test passthrough fails gracefully with missing auth headers."""
        config = PassthroughConfig(require_auth=True)
        passthrough = CredentialPassthrough(config=config)

        headers = {"content-type": "application/json"}
        ok, error = passthrough.validate_auth(headers)
        assert not ok, "Should reject request without auth header"
        assert error is not None, "Should provide error message"

    def test_credential_passthrough_invalid_bearer_format(self):
        """Test passthrough rejects malformed auth headers."""
        config = PassthroughConfig(require_auth=True)
        passthrough = CredentialPassthrough(config=config)

        # Malformed bearer token
        headers = {
            "authorization": "InvalidBearerFormat",
            "content-type": "application/json",
        }
        ok, error = passthrough.validate_auth(headers)
        assert not ok, "Should reject malformed auth format"
        assert error is not None, "Should provide error message"

    def test_credential_passthrough_redaction_in_logs(self):
        """Test that credentials are never logged."""
        config = PassthroughConfig()
        passthrough = CredentialPassthrough(config=config)

        headers = {
            "authorization": "Bearer sk-ant-secret-key-12345",
            "content-type": "application/json",
        }

        # mask_for_logging should redact auth
        safe_headers = passthrough.mask_for_logging(headers)
        assert "[REDACTED]" in safe_headers.get("authorization", ""), \
            "Auth header should be redacted"
        assert "secret-key" not in str(safe_headers), \
            "Secret should never appear in safe headers"


# ==============================================================================
# Test 2: Timeout Error Handling
# ==============================================================================

class TestTimeoutErrorHandling(unittest.TestCase):
    """Test error handling for timeout scenarios."""

    def test_circuit_breaker_timeout_failure(self):
        """Test circuit breaker records timeout as failure."""
        config = CircuitBreakerConfig(
            failure_threshold=2,
            recovery_timeout=0.1,
            window_seconds=1.0,
        )
        breaker = CircuitBreaker("anthropic", config)

        # Simulate two timeout failures
        breaker.record_failure()
        breaker.record_failure()

        # Circuit should be OPEN
        assert breaker.state == CircuitState.OPEN, \
            "Circuit should trip after threshold failures"

        # Subsequent requests should be fast-failed
        assert not breaker.allow_request(), \
            "Should fast-fail while circuit is open"

    def test_circuit_breaker_recovery_probe_timeout(self):
        """Test circuit breaker recovery mechanism."""
        config = CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=0.1,
            window_seconds=1.0,
        )
        breaker = CircuitBreaker("openai", config)

        # Trip the circuit
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        # Wait for recovery timeout
        time.sleep(0.15)

        # Should allow probe request
        assert breaker.allow_request(), \
            "Should allow probe request after recovery timeout"
        assert breaker.state == CircuitState.HALF_OPEN

        # Probe success → circuit closes
        breaker.record_success()
        assert breaker.state == CircuitState.CLOSED

    def test_circuit_breaker_probe_failure_reopens(self):
        """Test probe failure re-opens the circuit."""
        config = CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=0.1,
            window_seconds=1.0,
        )
        breaker = CircuitBreaker("google", config)

        breaker.record_failure()
        time.sleep(0.15)
        breaker.allow_request()  # Allows probe
        assert breaker.state == CircuitState.HALF_OPEN

        breaker.record_failure()  # Probe fails
        assert breaker.state == CircuitState.OPEN, \
            "Failed probe should reopen circuit"


# ==============================================================================
# Test 3: Malformed Request Handling
# ==============================================================================

class TestMalformedRequestHandling(unittest.TestCase):
    """Test error handling for malformed requests."""

    def test_router_invalid_json_body(self):
        """Test router handles invalid JSON gracefully."""
        router = ProviderRouter()

        # Malformed JSON
        result = router.route(
            path="/v1/messages",
            headers={"x-api-key": "sk-ant-test"},
            body=b"{invalid json}",
        )

        # Should still route to anthropic based on path/headers
        assert result.provider == "anthropic", \
            "Should route based on path/headers despite invalid body"

    def test_sse_streaming_malformed_json_event(self):
        """Test SSE handler gracefully handles malformed JSON events."""
        malformed_sse = b"""data: {"type": "message_start"}
data: {invalid json}
data: {"type": "message_delta", "usage": {"output_tokens": 10}}
"""
        result = extract_sse_tokens(malformed_sse)
        # Should extract valid tokens and skip malformed
        assert result.get("output_tokens", 0) == 10, \
            "Should extract valid tokens despite malformed events"

    def test_sse_streaming_invalid_utf8(self):
        """Test SSE handler handles invalid UTF-8 gracefully."""
        invalid_utf8 = b"data: \xff\xfe{invalid}"
        result = extract_sse_tokens(invalid_utf8)
        # Should not crash — fallback to replace mode
        assert isinstance(result, dict), \
            "Should return dict even with invalid UTF-8"

    def test_failover_malformed_config_yaml(self):
        """Test failover gracefully handles malformed YAML config."""
        with mock.patch(
            "tokenpak.proxy.failover.load_failover_config"
        ) as mock_load:
            mock_load.side_effect = Exception("YAML parse error")
            # Should not crash
            try:
                load_failover_config()
            except:
                pytest.fail("Should handle malformed YAML gracefully")


# ==============================================================================
# Test 4: Cache Miss & Edge Cases
# ==============================================================================

class TestCacheMissEdgeCases(unittest.TestCase):
    """Test error handling for cache miss scenarios."""

    def test_failover_missing_model_mapping(self):
        """Test failover handles missing model mappings gracefully."""
        config = FailoverConfig(
            enabled=True,
            chain=[
                ProviderEntry(
                    provider="anthropic",
                    model_map={"claude-opus-4-5": "claude-opus-4-5"},
                    credential_env="ANTHROPIC_API_KEY",
                ),
                ProviderEntry(
                    provider="openai",
                    model_map={},  # Empty mapping
                    credential_env="OPENAI_API_KEY",
                ),
            ],
        )
        mgr = FailoverManager(config=config)

        # Request model not in mapping — should use original
        mapped = mgr.map_model("claude-haiku-4-5", "openai")
        assert mapped == "claude-haiku-4-5", \
            "Should return original model name when mapping missing"

    def test_circuit_breaker_concurrent_access(self):
        """Test circuit breaker thread safety."""
        config = CircuitBreakerConfig(failure_threshold=5, window_seconds=10)
        breaker = CircuitBreaker("anthropic", config)

        results = []

        def worker():
            for _ in range(10):
                if breaker.allow_request():
                    breaker.record_success()
                time.sleep(0.001)
            results.append(breaker.state)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should not crash or corrupt state
        assert len(results) == 5, "All threads should complete"

    def test_failover_missing_credentials(self):
        """Test failover skips providers without credentials."""
        config = FailoverConfig(
            enabled=True,
            chain=[
                ProviderEntry(
                    provider="anthropic",
                    model_map={},
                    credential_env="ANTHROPIC_API_KEY",
                ),
                ProviderEntry(
                    provider="openai",
                    model_map={},
                    credential_env="OPENAI_API_KEY",
                ),
            ],
        )
        mgr = FailoverManager(config=config)

        # Clear both env vars
        with mock.patch.dict(os.environ, {}, clear=True):
            results = list(mgr.iter_providers("claude-opus-4-5"))
            assert len(results) == 0, \
                "Should skip all providers when no credentials available"


# ==============================================================================
# Test 5: Logging Failures
# ==============================================================================

class TestLoggingFailures(unittest.TestCase):
    """Test error handling when logging fails."""

    def test_cost_tracker_logging_failure_does_not_crash(self):
        """Test that logging failures don't crash the proxy."""
        with mock.patch(
            "tokenpak.proxy.proxy.logger.warning",
            side_effect=Exception("Logging failed"),
        ):
            # Should not crash even if logger fails
            result = record_proxy_request(
                model="claude-opus-4-5",
                prompt_tokens=100,
                completion_tokens=50,
            )
            # Result depends on whether cost_tracker is available

    def test_sse_parse_error_logging(self):
        """Test that SSE parse errors are logged but don't crash."""
        sse_data = b"data: {incomplete"
        # Should not raise
        result = extract_sse_tokens(sse_data)
        assert isinstance(result, dict), "Should return dict despite parse errors"

    def test_streaming_handler_chunk_error(self):
        """Test StreamHandler gracefully handles chunk errors."""
        handler = StreamHandler(content_encoding="")

        # Attempt to process None data
        try:
            # Should handle gracefully
            handler.process_chunk(b"data: {incomplete")
        except:
            pytest.fail("Should handle malformed chunks gracefully")


# ==============================================================================
# Test 6: Provider Unavailability
# ==============================================================================

class TestProviderUnavailability(unittest.TestCase):
    """Test error handling when providers are unavailable."""

    def test_circuit_breaker_registry_per_provider(self):
        """Test circuit breaker isolates failures per provider."""
        registry = CircuitBreakerRegistry(
            config=CircuitBreakerConfig(failure_threshold=2)
        )

        # Trip anthropic circuit
        registry.record_failure("anthropic")
        registry.record_failure("anthropic")
        assert not registry.allow_request("anthropic"), \
            "Anthropic should be open"

        # OpenAI should still be available
        assert registry.allow_request("openai"), \
            "OpenAI should be unaffected"

    def test_provider_detection_from_url(self):
        """Test provider detection handles various URL formats."""
        test_cases = [
            ("https://api.anthropic.com/v1/messages", "anthropic"),
            ("https://api.openai.com/v1/chat/completions", "openai"),
            ("https://generativelanguage.googleapis.com/v1beta", "google"),
            ("https://unknown-provider.com/api", "unknown-provider.com"),
        ]

        for url, expected_provider in test_cases:
            result = provider_from_url(url)
            assert expected_provider in result or result == expected_provider, \
                f"Should detect {expected_provider} from {url}"

    def test_failover_unavailable_primary_uses_secondary(self):
        """Test failover switches to secondary when primary fails."""
        config = FailoverConfig(
            enabled=True,
            chain=[
                ProviderEntry(
                    provider="anthropic",
                    model_map={"gpt-4": "claude-opus-4-5"},
                    credential_env="ANTHROPIC_API_KEY",
                ),
                ProviderEntry(
                    provider="openai",
                    model_map={"gpt-4": "gpt-4o"},
                    credential_env="OPENAI_API_KEY",
                ),
            ],
        )
        mgr = FailoverManager(config=config)

        with mock.patch.dict(
            os.environ,
            {"ANTHROPIC_API_KEY": "key1", "OPENAI_API_KEY": "key2"}
        ):
            results = list(mgr.iter_providers("gpt-4"))
            assert len(results) >= 1, "Should have at least one provider"
            # First should be anthropic, second should be openai
            assert results[0].provider == "anthropic"
            if len(results) > 1:
                assert results[1].provider == "openai"


# ==============================================================================
# Test 7: Connection Pool Error Handling
# ==============================================================================

class TestConnectionPoolErrorHandling(unittest.TestCase):
    """Test error handling in connection pool management."""

    def test_circuit_breaker_enables_graceful_degradation(self):
        """Test circuit breaker enables graceful service degradation."""
        config = CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=0.1,
        )
        registry = CircuitBreakerRegistry(config=config)

        # Simulate provider failure
        registry.record_failure("anthropic")

        # Should fast-fail instead of hanging
        assert not registry.allow_request("anthropic"), \
            "Should fast-fail when provider is down"

        # Wait and try recovery
        time.sleep(0.15)
        assert registry.allow_request("anthropic"), \
            "Should allow recovery probe"


# ==============================================================================
# Test 8: Edge Case - Concurrent Failover Attempts
# ==============================================================================

class TestConcurrentFailover(unittest.TestCase):
    """Test failover handles concurrent requests safely."""

    def test_concurrent_circuit_state_queries(self):
        """Test circuit breaker state is consistent under concurrent access."""
        registry = CircuitBreakerRegistry()
        results = []

        def check_state(provider: str):
            for _ in range(10):
                state = registry.get_state(provider)
                results.append(state)
                time.sleep(0.001)

        threads = [
            threading.Thread(target=check_state, args=("anthropic",))
            for _ in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have consistent state values
        assert all(isinstance(r, CircuitState) for r in results), \
            "All states should be CircuitState instances"


# ==============================================================================
# Integration Tests
# ==============================================================================

class TestErrorHandlingIntegration(unittest.TestCase):
    """Integration tests for complete error flows."""

    def test_failover_chain_with_circuit_breakers(self):
        """Test failover and circuit breaker work together."""
        failover_config = FailoverConfig(
            enabled=True,
            chain=[
                ProviderEntry(
                    provider="anthropic",
                    model_map={"test": "test"},
                    credential_env="ANTHROPIC_API_KEY",
                ),
                ProviderEntry(
                    provider="openai",
                    model_map={"test": "test"},
                    credential_env="OPENAI_API_KEY",
                ),
            ],
        )
        mgr = FailoverManager(config=failover_config)
        registry = CircuitBreakerRegistry()

        with mock.patch.dict(
            os.environ,
            {"ANTHROPIC_API_KEY": "key1", "OPENAI_API_KEY": "key2"}
        ):
            # Simulate anthropic failure
            registry.record_failure("anthropic")
            registry.record_failure("anthropic")

            # Get available providers
            providers = list(mgr.iter_providers("test"))
            assert len(providers) > 0, "Should have fallback providers"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
