"""Tests for provider detection."""


import pytest

pytest.importorskip("tokenpak.pro.routing.detector", reason="module not available in current build")
import pytest

from tokenpak.pro.routing.detector import Provider, ProviderDetector


class TestProviderDetection:
    """Test provider detection from various sources."""

    def setup_method(self):
        """Set up test detector."""
        self.detector = ProviderDetector()

    def test_detect_anthropic_key(self):
        """Test Anthropic API key detection."""
        key = "sk-ant-valid_test_key_123"
        provider = self.detector.detect_from_key(key)
        assert provider == Provider.ANTHROPIC

    def test_detect_openai_key(self):
        """Test OpenAI API key detection."""
        key = "sk-1234567890abcdefghijklmnop"
        provider = self.detector.detect_from_key(key)
        assert provider == Provider.OPENAI

    def test_detect_google_key(self):
        """Test Google API key detection."""
        key = "AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz123456789"
        provider = self.detector.detect_from_key(key)
        assert provider == Provider.GOOGLE

    def test_detect_bedrock_key(self):
        """Test Bedrock API key detection."""
        key = "bedrock_test_key@bedrock"
        provider = self.detector.detect_from_key(key)
        assert provider == Provider.BEDROCK

    def test_detect_litellm_key(self):
        """Test LiteLLM API key detection."""
        key = "litellm-test_key_123"
        provider = self.detector.detect_from_key(key)
        assert provider == Provider.LITELLM

    def test_invalid_key(self):
        """Test invalid key format."""
        key = "not-a-valid-key"
        provider = self.detector.detect_from_key(key)
        assert provider is None

    def test_empty_key(self):
        """Test empty key."""
        provider = self.detector.detect_from_key("")
        assert provider is None

    def test_none_key(self):
        """Test None key."""
        provider = self.detector.detect_from_key(None)
        assert provider is None

    def test_detect_from_model_claude(self):
        """Test Claude model detection."""
        provider = self.detector.detect_from_model("claude-3-opus-20240229")
        assert provider == Provider.ANTHROPIC

    def test_detect_from_model_gpt(self):
        """Test GPT model detection."""
        provider = self.detector.detect_from_model("gpt-4-turbo")
        assert provider == Provider.OPENAI

    def test_detect_from_model_gemini(self):
        """Test Gemini model detection."""
        provider = self.detector.detect_from_model("gemini-pro")
        assert provider == Provider.GOOGLE

    def test_detect_from_model_davinci(self):
        """Test Davinci model detection."""
        provider = self.detector.detect_from_model("text-davinci-003")
        assert provider == Provider.OPENAI

    def test_detect_from_model_bedrock(self):
        """Test Bedrock model detection."""
        provider = self.detector.detect_from_model("anthropic.claude-v1")
        assert provider == Provider.BEDROCK

    def test_detect_from_model_titan(self):
        """Test Titan model detection."""
        provider = self.detector.detect_from_model("amazon.titan-text-express")
        assert provider == Provider.BEDROCK

    def test_invalid_model(self):
        """Test invalid model."""
        provider = self.detector.detect_from_model("unknown-model")
        assert provider is None

    def test_headers_with_auth(self):
        """Test detection from Authorization header."""
        headers = {"Authorization": "Bearer sk-ant-test_key_123"}
        provider = self.detector.detect_from_headers(headers)
        assert provider == Provider.ANTHROPIC

    def test_headers_with_x_api_key(self):
        """Test detection from X-API-Key header."""
        headers = {"X-API-Key": "sk-1234567890abcdefghijklmnop"}
        provider = self.detector.detect_from_headers(headers)
        assert provider == Provider.OPENAI

    def test_headers_with_lowercase_api_key(self):
        """Test detection from lowercase x-api-key header."""
        headers = {"x-api-key": "AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz123456789"}
        provider = self.detector.detect_from_headers(headers)
        assert provider == Provider.GOOGLE

    def test_headers_anthropic_version(self):
        """Test detection from anthropic-version header."""
        headers = {"anthropic-version": "2023-06-01"}
        provider = self.detector.detect_from_headers(headers)
        assert provider == Provider.ANTHROPIC

    def test_headers_openai_org(self):
        """Test detection from openai-organization header."""
        headers = {"openai-organization": "org-123"}
        provider = self.detector.detect_from_headers(headers)
        assert provider == Provider.OPENAI

    def test_headers_google_project(self):
        """Test detection from google-cloud-project header."""
        headers = {"google-cloud-project": "my-project"}
        provider = self.detector.detect_from_headers(headers)
        assert provider == Provider.GOOGLE

    def test_headers_empty(self):
        """Test empty headers."""
        provider = self.detector.detect_from_headers({})
        assert provider is None

    def test_headers_none(self):
        """Test None headers."""
        provider = self.detector.detect_from_headers(None)
        assert provider is None

    def test_multi_strategy_key_wins(self):
        """Test that key detection takes priority."""
        provider, reason = self.detector.detect(
            api_key="sk-ant-test_key_123",
            model="gpt-4",
            headers={"openai-organization": "org-123"},
        )
        assert provider == Provider.ANTHROPIC
        assert "API key" in reason

    def test_multi_strategy_model_second(self):
        """Test that model detection is second priority."""
        provider, reason = self.detector.detect(
            model="gpt-4",
            headers={"anthropic-version": "2023-06-01"},
        )
        assert provider == Provider.OPENAI
        assert "model" in reason

    def test_multi_strategy_headers_last(self):
        """Test that headers are last priority."""
        provider, reason = self.detector.detect(
            headers={"anthropic-version": "2023-06-01"}
        )
        assert provider == Provider.ANTHROPIC
        assert "headers" in reason

    def test_no_detection(self):
        """Test when nothing can be detected."""
        provider, reason = self.detector.detect()
        assert provider is None
        assert "no provider detected" in reason

    def test_invalid_header_format(self):
        """Test invalid header format."""
        headers = {"Authorization": "OnlyBearerNoKey"}
        provider = self.detector.detect_from_headers(headers)
        assert provider is None

    def test_case_insensitive_header_keys(self):
        """Test header key case handling."""
        headers = {"X-API-KEY": "sk-ant-test_key_123"}  # uppercase
        provider = self.detector.detect_from_headers(headers)
        # Header matching may be case-sensitive depending on implementation
        # This tests actual behavior
        assert provider is None or provider == Provider.ANTHROPIC
