"""Test Google adapter tool validation."""

import pytest

from tokenpak.proxy.adapters.canonical import CanonicalRequest
from tokenpak.proxy.adapters.google_adapter import GoogleGenerativeAIAdapter


class TestGoogleAdapterTools:
    """Test Google adapter function calling support (or lack thereof)."""

    def test_google_adapter_no_tools_passes(self):
        """Test that requests without tools work normally."""
        adapter = GoogleGenerativeAIAdapter()
        canonical = CanonicalRequest(
            model="gemini-pro",
            system="You are helpful.",
            messages=[{"role": "user", "content": "Hello"}],
            tools=None,  # No tools
            generation={},
            stream=False,
            raw_extra={},
            source_format="google-generative-ai",
        )
        # Should not raise
        result = adapter.denormalize(canonical)
        assert result is not None
        assert b"gemini-pro" in result

    def test_google_adapter_empty_tools_passes(self):
        """Test that empty tools array passes."""
        adapter = GoogleGenerativeAIAdapter()
        canonical = CanonicalRequest(
            model="gemini-pro",
            system="",
            messages=[{"role": "user", "content": "Hello"}],
            tools=[],  # Empty tools
            generation={},
            stream=False,
            raw_extra={},
            source_format="google-generative-ai",
        )
        # Should not raise
        result = adapter.denormalize(canonical)
        assert result is not None

    def test_google_adapter_with_tools_raises(self):
        """Test that requests with tools raise NotImplementedError."""
        adapter = GoogleGenerativeAIAdapter()
        canonical = CanonicalRequest(
            model="gemini-pro",
            system="",
            messages=[{"role": "user", "content": "Call a function"}],
            tools=[
                {
                    "name": "get_weather",
                    "description": "Get the weather for a location",
                    "input_schema": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                    },
                }
            ],
            generation={},
            stream=False,
            raw_extra={},
            source_format="google-generative-ai",
        )
        with pytest.raises(NotImplementedError, match="function calling"):
            adapter.denormalize(canonical)

    def test_google_adapter_tool_error_message_helpful(self):
        """Test that error message provides actionable guidance."""
        adapter = GoogleGenerativeAIAdapter()
        canonical = CanonicalRequest(
            model="gemini-pro",
            system="",
            messages=[{"role": "user", "content": "Call a tool"}],
            tools=[{"name": "test_tool"}],
            generation={},
            stream=False,
            raw_extra={},
            source_format="google-generative-ai",
        )
        with pytest.raises(NotImplementedError) as exc_info:
            adapter.denormalize(canonical)

        error_msg = str(exc_info.value)
        # Check for key guidance in error message
        assert "OpenAI" in error_msg or "Anthropic" in error_msg
        assert "Workaround" in error_msg
        assert "adapter" in error_msg.lower()
