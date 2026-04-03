"""
Unit tests for TokenPak Anthropic Adapter.

Tests cover:
- Request validation and preparation
- HTTP transport (mocked)
- Response parsing and error handling
- Token extraction from usage blocks
- Timeout and authentication error handling
"""

import json
import unittest
from unittest.mock import MagicMock, Mock, patch
import requests

from tokenpak.adapters.anthropic import AnthropicAdapter
from tokenpak.adapters.base import (
    TokenPakAdapterError,
    TokenPakAuthError,
    TokenPakConfigError,
    TokenPakTimeoutError,
)


class TestAnthropicAdapterInit(unittest.TestCase):
    """Test AnthropicAdapter initialization."""

    def test_init_valid(self):
        """Test successful initialization with valid parameters."""
        adapter = AnthropicAdapter(
            base_url="http://127.0.0.1:8767",
            api_key="sk-ant-test123",
        )
        self.assertEqual(adapter.base_url, "http://127.0.0.1:8767")
        self.assertEqual(adapter.api_key, "sk-ant-test123")
        self.assertEqual(adapter.timeout_s, 120.0)
        self.assertEqual(adapter.provider_name, "anthropic")

    def test_init_custom_timeout(self):
        """Test initialization with custom timeout."""
        adapter = AnthropicAdapter(
            base_url="http://127.0.0.1:8767",
            api_key="sk-ant-test",
            timeout_s=30.0,
        )
        self.assertEqual(adapter.timeout_s, 30.0)

    def test_init_empty_base_url(self):
        """Test that empty base_url raises TokenPakConfigError."""
        with self.assertRaises(TokenPakConfigError):
            AnthropicAdapter(base_url="", api_key="sk-ant-test")

    def test_init_empty_api_key(self):
        """Test that empty api_key raises TokenPakConfigError."""
        with self.assertRaises(TokenPakConfigError):
            AnthropicAdapter(base_url="http://127.0.0.1:8767", api_key="")

    def test_init_strips_trailing_slash(self):
        """Test that base_url trailing slash is stripped."""
        adapter = AnthropicAdapter(
            base_url="http://127.0.0.1:8767/",
            api_key="sk-ant-test",
        )
        self.assertEqual(adapter.base_url, "http://127.0.0.1:8767")


class TestPrepareRequest(unittest.TestCase):
    """Test AnthropicAdapter.prepare_request()."""

    def setUp(self):
        self.adapter = AnthropicAdapter(
            base_url="http://127.0.0.1:8767",
            api_key="sk-ant-test",
        )

    def test_prepare_valid_request(self):
        """Test preparation of a valid minimal request."""
        request = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Hello"}],
        }
        prepared = self.adapter.prepare_request(request)
        self.assertEqual(prepared["model"], "claude-3-5-sonnet-20241022")
        self.assertEqual(prepared["max_tokens"], 1024)
        self.assertEqual(prepared["stream"], False)  # defaults to False

    def test_prepare_preserves_stream_true(self):
        """Test that stream=True is preserved if explicitly set."""
        request = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        }
        prepared = self.adapter.prepare_request(request)
        self.assertTrue(prepared["stream"])

    def test_prepare_missing_model(self):
        """Test that missing model raises TokenPakConfigError."""
        request = {
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Hello"}],
        }
        with self.assertRaises(TokenPakConfigError) as cm:
            self.adapter.prepare_request(request)
        self.assertIn("model", str(cm.exception))

    def test_prepare_missing_max_tokens(self):
        """Test that missing max_tokens raises TokenPakConfigError."""
        request = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        with self.assertRaises(TokenPakConfigError) as cm:
            self.adapter.prepare_request(request)
        self.assertIn("max_tokens", str(cm.exception))

    def test_prepare_missing_messages(self):
        """Test that missing messages raises TokenPakConfigError."""
        request = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
        }
        with self.assertRaises(TokenPakConfigError) as cm:
            self.adapter.prepare_request(request)
        self.assertIn("messages", str(cm.exception))

    def test_prepare_empty_messages(self):
        """Test that empty messages list raises TokenPakConfigError."""
        request = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "messages": [],
        }
        with self.assertRaises(TokenPakConfigError):
            self.adapter.prepare_request(request)

    def test_prepare_messages_not_list(self):
        """Test that non-list messages raises TokenPakConfigError."""
        request = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "messages": {"role": "user", "content": "Hello"},
        }
        with self.assertRaises(TokenPakConfigError):
            self.adapter.prepare_request(request)

    def test_prepare_message_missing_role(self):
        """Test that message without role raises TokenPakConfigError."""
        request = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "messages": [{"content": "Hello"}],
        }
        with self.assertRaises(TokenPakConfigError) as cm:
            self.adapter.prepare_request(request)
        self.assertIn("messages[0]", str(cm.exception))

    def test_prepare_message_missing_content(self):
        """Test that message without content raises TokenPakConfigError."""
        request = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "messages": [{"role": "user"}],
        }
        with self.assertRaises(TokenPakConfigError) as cm:
            self.adapter.prepare_request(request)
        self.assertIn("messages[0]", str(cm.exception))

    def test_prepare_preserves_extra_fields(self):
        """Test that extra fields are preserved (proxy decides what to accept)."""
        request = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Hello"}],
            "system": "You are helpful.",
            "temperature": 0.7,
        }
        prepared = self.adapter.prepare_request(request)
        self.assertEqual(prepared["system"], "You are helpful.")
        self.assertEqual(prepared["temperature"], 0.7)


class TestSend(unittest.TestCase):
    """Test AnthropicAdapter.send()."""

    def setUp(self):
        self.adapter = AnthropicAdapter(
            base_url="http://127.0.0.1:8767",
            api_key="sk-ant-test",
            timeout_s=30.0,
        )

    @patch("tokenpak.adapters.anthropic._requests")
    def test_send_successful(self, mock_requests):
        """Test successful HTTP request."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "id": "msg_123",
            "type": "message",
            "content": [{"type": "text", "text": "Hello, world!"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        mock_requests.post.return_value = mock_resp

        prepared = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Hello"}],
        }
        response = self.adapter.send(prepared)

        self.assertEqual(response["id"], "msg_123")
        self.assertEqual(response["content"][0]["text"], "Hello, world!")

        # Verify request was made with correct headers
        call_args = mock_requests.post.call_args
        self.assertEqual(call_args[1]["headers"]["x-api-key"], "sk-ant-test")
        self.assertEqual(call_args[1]["timeout"], 30.0)

    @patch("tokenpak.adapters.anthropic._requests.post")
    def test_send_timeout(self, mock_post):
        """Test that timeout raises TokenPakTimeoutError."""
        mock_post.side_effect = requests.exceptions.Timeout(
            "Connection timed out"
        )

        prepared = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Hello"}],
        }
        with self.assertRaises(TokenPakTimeoutError):
            self.adapter.send(prepared)

    @patch("tokenpak.adapters.anthropic._requests")
    def test_send_auth_error_401(self, mock_requests):
        """Test that 401 response raises TokenPakAuthError."""
        mock_resp = Mock()
        mock_resp.status_code = 401
        mock_requests.post.return_value = mock_resp

        prepared = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Hello"}],
        }
        with self.assertRaises(TokenPakAuthError):
            self.adapter.send(prepared)

    @patch("tokenpak.adapters.anthropic._requests")
    def test_send_auth_error_403(self, mock_requests):
        """Test that 403 response raises TokenPakAuthError."""
        mock_resp = Mock()
        mock_resp.status_code = 403
        mock_requests.post.return_value = mock_resp

        prepared = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Hello"}],
        }
        with self.assertRaises(TokenPakAuthError):
            self.adapter.send(prepared)

    @patch("tokenpak.adapters.anthropic._requests")
    def test_send_http_error_500(self, mock_requests):
        """Test that 5xx response raises TokenPakAdapterError."""
        mock_resp = Mock()
        mock_resp.status_code = 500
        mock_resp.ok = False
        mock_resp.json.return_value = {"error": {"message": "Internal server error"}}
        mock_requests.post.return_value = mock_resp

        prepared = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Hello"}],
        }
        with self.assertRaises(TokenPakAdapterError):
            self.adapter.send(prepared)

    @patch("tokenpak.adapters.anthropic._requests")
    def test_send_invalid_json_response(self, mock_requests):
        """Test that invalid JSON response raises TokenPakAdapterError."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.json.side_effect = ValueError("Invalid JSON")
        mock_requests.post.return_value = mock_resp

        prepared = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Hello"}],
        }
        with self.assertRaises(TokenPakAdapterError):
            self.adapter.send(prepared)

    @patch("tokenpak.adapters.anthropic._requests.post")
    def test_send_request_exception(self, mock_post):
        """Test that generic RequestException raises TokenPakAdapterError."""
        mock_post.side_effect = requests.exceptions.RequestException(
            "Connection refused"
        )

        prepared = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Hello"}],
        }
        with self.assertRaises(TokenPakAdapterError):
            self.adapter.send(prepared)


class TestParseResponse(unittest.TestCase):
    """Test AnthropicAdapter.parse_response()."""

    def setUp(self):
        self.adapter = AnthropicAdapter(
            base_url="http://127.0.0.1:8767",
            api_key="sk-ant-test",
        )

    def test_parse_valid_response(self):
        """Test parsing a valid response."""
        response = {
            "id": "msg_123",
            "type": "message",
            "content": [{"type": "text", "text": "Hello"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        result = self.adapter.parse_response(response)
        self.assertEqual(result["id"], "msg_123")

    def test_parse_response_with_error_dict(self):
        """Test that response with error dict raises TokenPakAdapterError."""
        response = {
            "error": {
                "type": "invalid_request_error",
                "message": "Model not found",
            }
        }
        with self.assertRaises(TokenPakAdapterError) as cm:
            self.adapter.parse_response(response)
        self.assertIn("invalid_request_error", str(cm.exception))
        self.assertIn("Model not found", str(cm.exception))

    def test_parse_response_with_error_string(self):
        """Test that response with error string raises TokenPakAdapterError."""
        response = {"error": "Something went wrong"}
        with self.assertRaises(TokenPakAdapterError):
            self.adapter.parse_response(response)

    def test_parse_response_preserves_input(self):
        """Test that parse_response returns input unchanged on success."""
        response = {
            "id": "msg_123",
            "type": "message",
            "content": [{"type": "text", "text": "Test"}],
        }
        result = self.adapter.parse_response(response)
        self.assertIs(result, response)


class TestExtractTokens(unittest.TestCase):
    """Test AnthropicAdapter.extract_tokens()."""

    def setUp(self):
        self.adapter = AnthropicAdapter(
            base_url="http://127.0.0.1:8767",
            api_key="sk-ant-test",
        )

    def test_extract_tokens_complete(self):
        """Test token extraction from complete usage block."""
        response = {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 10,
                "cache_creation_input_tokens": 5,
            }
        }
        tokens = self.adapter.extract_tokens(response)
        self.assertEqual(tokens["input_tokens"], 100)
        self.assertEqual(tokens["output_tokens"], 50)
        self.assertEqual(tokens["cache_read"], 10)
        self.assertEqual(tokens["cache_write"], 5)
        self.assertEqual(tokens["total"], 150)

    def test_extract_tokens_partial_usage(self):
        """Test token extraction with partial usage block."""
        response = {
            "usage": {
                "input_tokens": 50,
                "output_tokens": 25,
            }
        }
        tokens = self.adapter.extract_tokens(response)
        self.assertEqual(tokens["input_tokens"], 50)
        self.assertEqual(tokens["output_tokens"], 25)
        self.assertEqual(tokens["cache_read"], 0)
        self.assertEqual(tokens["cache_write"], 0)
        self.assertEqual(tokens["total"], 75)

    def test_extract_tokens_missing_usage(self):
        """Test that missing usage block returns zeros with warning."""
        response = {"id": "msg_123", "type": "message"}
        tokens = self.adapter.extract_tokens(response)
        self.assertEqual(tokens["input_tokens"], 0)
        self.assertEqual(tokens["output_tokens"], 0)
        self.assertEqual(tokens["cache_read"], 0)
        self.assertEqual(tokens["cache_write"], 0)
        self.assertEqual(tokens["total"], 0)

    def test_extract_tokens_empty_usage(self):
        """Test that empty usage block returns zeros."""
        response = {"usage": {}}
        tokens = self.adapter.extract_tokens(response)
        self.assertEqual(tokens["input_tokens"], 0)
        self.assertEqual(tokens["output_tokens"], 0)
        self.assertEqual(tokens["total"], 0)

    def test_extract_tokens_string_values(self):
        """Test that string token values are coerced to int."""
        response = {
            "usage": {
                "input_tokens": "100",
                "output_tokens": "50",
                "cache_read_input_tokens": "10",
                "cache_creation_input_tokens": "5",
            }
        }
        tokens = self.adapter.extract_tokens(response)
        self.assertEqual(tokens["input_tokens"], 100)
        self.assertEqual(tokens["output_tokens"], 50)
        self.assertIsInstance(tokens["input_tokens"], int)


class TestIntegration(unittest.TestCase):
    """Integration tests for the full request/response cycle."""

    @patch("tokenpak.adapters.anthropic._requests")
    def test_full_cycle(self, mock_requests):
        """Test complete prepare → send → parse → extract cycle."""
        adapter = AnthropicAdapter(
            base_url="http://127.0.0.1:8767",
            api_key="sk-ant-test",
        )

        # Mock response
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "id": "msg_456",
            "type": "message",
            "content": [{"type": "text", "text": "Response text"}],
            "usage": {
                "input_tokens": 20,
                "output_tokens": 15,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }
        mock_requests.post.return_value = mock_resp

        # Step 1: prepare
        request = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Test"}],
        }
        prepared = adapter.prepare_request(request)
        self.assertIsNotNone(prepared)

        # Step 2: send
        response = adapter.send(prepared)
        self.assertEqual(response["id"], "msg_456")

        # Step 3: parse
        parsed = adapter.parse_response(response)
        self.assertEqual(parsed["id"], "msg_456")

        # Step 4: extract tokens
        tokens = adapter.extract_tokens(parsed)
        self.assertEqual(tokens["input_tokens"], 20)
        self.assertEqual(tokens["output_tokens"], 15)
        self.assertEqual(tokens["total"], 35)


if __name__ == "__main__":
    unittest.main()
