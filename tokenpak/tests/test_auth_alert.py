"""Unit tests for tokenpak.security.auth_alert module."""

import json
import logging
from unittest import mock
from urllib.error import HTTPError, URLError

import pytest

from tokenpak.security.auth_alert import (
    NullNotificationHook,
    WebhookNotificationHook,
    _build_alert_message,
    register_auth_alert_hook,
)


class TestWebhookNotificationHook:
    """Test WebhookNotificationHook — the primary HTTP webhook implementation."""

    def test_webhook_success_on_auth_failure_event(self):
        """Happy path: successful POST to webhook URL on auth-failure-detected event."""
        hook = WebhookNotificationHook(
            url="https://example.com/alerts",
            headers={"Authorization": "Bearer token123"},
        )
        
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = mock.Mock()
            mock_response.getcode.return_value = 200
            mock_response.__enter__ = mock.Mock(return_value=mock_response)
            mock_response.__exit__ = mock.Mock(return_value=None)
            mock_urlopen.return_value = mock_response
            
            hook("anthropic", "auth-failure-detected", {
                "consecutive_failures": 3,
                "timestamp": "2026-03-27T14:10:00Z",
            })
            
            # Verify urlopen was called once
            assert mock_urlopen.call_count == 1
            call_args = mock_urlopen.call_args
            request = call_args[0][0]
            
            # Check request method and URL
            assert request.full_url == "https://example.com/alerts"
            assert request.get_method() == "POST"
            
            # Verify payload structure
            payload = json.loads(request.data.decode())
            assert payload["event"] == "auth-failure-detected"
            assert payload["provider"] == "anthropic"
            assert payload["details"]["consecutive_failures"] == 3

    def test_webhook_ignores_non_auth_failure_events(self):
        """Webhook should silently ignore events other than 'auth-failure-detected'."""
        hook = WebhookNotificationHook(url="https://example.com/alerts")
        
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            hook("anthropic", "some-other-event", {})
            
            # Should not POST for non-auth-failure events
            assert mock_urlopen.call_count == 0

    def test_webhook_with_custom_headers(self):
        """Custom headers in constructor should be merged into the POST request."""
        hook = WebhookNotificationHook(
            url="https://example.com/alerts",
            headers={"X-Custom": "value123", "Authorization": "Bearer xyz"},
        )
        
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = mock.Mock()
            mock_response.getcode.return_value = 201
            mock_response.__enter__ = mock.Mock(return_value=mock_response)
            mock_response.__exit__ = mock.Mock(return_value=None)
            mock_urlopen.return_value = mock_response
            
            hook("gemini", "auth-failure-detected", {"consecutive_failures": 5})
            
            request = mock_urlopen.call_args[0][0]
            headers = request.headers
            
            assert headers["X-custom"] == "value123"  # Note: headers are lowercased
            assert headers["Authorization"] == "Bearer xyz"
            assert headers["Content-type"] == "application/json"

    def test_webhook_http_error_logging(self, caplog):
        """HTTP errors (non-2xx) should be logged but not raise."""
        hook = WebhookNotificationHook(url="https://example.com/alerts")
        
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = mock.Mock()
            mock_response.getcode.return_value = 500
            mock_response.__enter__ = mock.Mock(return_value=mock_response)
            mock_response.__exit__ = mock.Mock(return_value=None)
            mock_urlopen.return_value = mock_response
            
            with caplog.at_level(logging.WARNING):
                hook("anthropic", "auth-failure-detected", {})
            
            # Should log warning about HTTP 500
            assert "HTTP 500" in caplog.text or "500" in caplog.text

    def test_webhook_network_error_logging(self, caplog):
        """Network errors (URLError, timeout) should be caught and logged."""
        hook = WebhookNotificationHook(url="https://example.com/alerts", timeout=5)
        
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = URLError("Connection refused")
            
            with caplog.at_level(logging.ERROR):
                hook("anthropic", "auth-failure-detected", {})
            
            # Should log error about delivery failure
            assert "delivery failed" in caplog.text.lower() or "error" in caplog.text.lower()

    def test_webhook_timeout_parameter(self):
        """Custom timeout should be passed to urlopen."""
        hook = WebhookNotificationHook(url="https://example.com/alerts", timeout=30)
        
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = mock.Mock()
            mock_response.getcode.return_value = 200
            mock_response.__enter__ = mock.Mock(return_value=mock_response)
            mock_response.__exit__ = mock.Mock(return_value=None)
            mock_urlopen.return_value = mock_response
            
            hook("anthropic", "auth-failure-detected", {})
            
            # Timeout should be passed as keyword arg
            assert mock_urlopen.call_args[1]["timeout"] == 30

    def test_webhook_payload_includes_alert_message(self):
        """Payload should include human-readable alert message."""
        hook = WebhookNotificationHook(url="https://example.com/alerts")
        
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = mock.Mock()
            mock_response.getcode.return_value = 200
            mock_response.__enter__ = mock.Mock(return_value=mock_response)
            mock_response.__exit__ = mock.Mock(return_value=None)
            mock_urlopen.return_value = mock_response
            
            hook("anthropic", "auth-failure-detected", {
                "consecutive_failures": 2,
                "timestamp": "2026-03-27T14:10:00Z",
            })
            
            payload = json.loads(mock_urlopen.call_args[0][0].data.decode())
            assert "message" in payload
            assert "TokenPak Auth Failure" in payload["message"]
            assert "Anthropic" in payload["message"]


class TestNullNotificationHook:
    """Test NullNotificationHook — the no-op implementation."""

    def test_null_hook_accepts_any_event_silently(self):
        """NullNotificationHook should accept all events without raising."""
        hook = NullNotificationHook()
        
        # Should not raise on any event
        hook("anthropic", "auth-failure-detected", {})
        hook("gemini", "some-event", {"key": "value"})
        hook("unknown_provider", "unknown_event", None)

    def test_null_hook_logs_debug_message(self, caplog):
        """NullNotificationHook should log debug-level messages."""
        hook = NullNotificationHook()
        
        with caplog.at_level(logging.DEBUG):
            hook("anthropic", "auth-failure-detected", {})
        
        # Should contain event and provider in log
        assert "NullNotificationHook" in caplog.text
        assert "auth-failure-detected" in caplog.text


class TestBuildAlertMessage:
    """Test _build_alert_message — the human-readable message formatter."""

    def test_alert_message_with_all_details(self):
        """Happy path: full alert message with all details."""
        msg = _build_alert_message("anthropic", {
            "consecutive_failures": 5,
            "timestamp": "2026-03-27T14:10:00Z",
        })
        
        assert "TokenPak Auth Failure" in msg
        assert "Anthropic" in msg
        assert "5 consecutive" in msg
        assert "2026-03-27T14:10:00Z" in msg

    def test_alert_message_with_missing_consecutive_failures(self):
        """Alert message should handle missing consecutive_failures gracefully."""
        msg = _build_alert_message("gemini", {
            "timestamp": "2026-03-27T14:10:00Z",
        })
        
        assert "TokenPak Auth Failure" in msg
        assert "Gemini" in msg
        assert "?" in msg  # Default placeholder

    def test_alert_message_with_missing_timestamp(self):
        """Alert message should handle missing timestamp gracefully."""
        msg = _build_alert_message("anthropic", {
            "consecutive_failures": 3,
        })
        
        assert "TokenPak Auth Failure" in msg
        assert "unknown" in msg.lower()

    def test_alert_message_empty_details(self):
        """Alert message should handle empty details dict."""
        msg = _build_alert_message("anthropic", {})
        
        assert "TokenPak Auth Failure" in msg
        assert "Anthropic" in msg
        assert "unknown" in msg.lower()

    def test_alert_message_provider_capitalization(self):
        """Provider name should be capitalized in the message."""
        for provider in ["anthropic", "gemini", "claude", "openai"]:
            msg = _build_alert_message(provider, {})
            expected = provider.capitalize()
            assert expected in msg


class TestRegisterAuthAlertHook:
    """Test register_auth_alert_hook — the registration function."""

    def test_register_webhook_hook(self):
        """Should be able to register a WebhookNotificationHook."""
        hook = WebhookNotificationHook(url="https://example.com/alerts")
        
        # Patch the lazy import inside register_auth_alert_hook
        with mock.patch("tokenpak.security.auth_guard.AUTH_GUARD") as mock_guard:
            register_auth_alert_hook(hook)
            
            # AUTH_GUARD.on_auth_failure should have been called
            mock_guard.on_auth_failure.assert_called_once_with(hook)

    def test_register_null_hook(self):
        """Should be able to register a NullNotificationHook."""
        hook = NullNotificationHook()
        
        with mock.patch("tokenpak.security.auth_guard.AUTH_GUARD") as mock_guard:
            register_auth_alert_hook(hook)
            
            mock_guard.on_auth_failure.assert_called_once_with(hook)

    def test_register_custom_callable(self):
        """Should be able to register any callable with correct signature."""
        def my_handler(provider: str, event: str, details: dict) -> None:
            pass
        
        with mock.patch("tokenpak.security.auth_guard.AUTH_GUARD") as mock_guard:
            register_auth_alert_hook(my_handler)
            
            mock_guard.on_auth_failure.assert_called_once_with(my_handler)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
