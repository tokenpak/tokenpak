"""Tests for Google adapter function calling stub."""

from __future__ import annotations

import json
import pytest

from tokenpak.proxy.adapters import GoogleGenerativeAIAdapter
from tokenpak.proxy.adapters.canonical import CanonicalRequest


class TestGoogleAdapterToolsStub:
    """Google adapter function calling support — currently a NotImplementedError stub."""

    def setup_method(self):
        self.adapter = GoogleGenerativeAIAdapter()

    def test_google_adapter_raises_not_implemented_on_tools(self):
        """Should raise NotImplementedError when tools are requested."""
        canonical = CanonicalRequest(
            model="gemini-2-flash",
            system="You are helpful.",
            messages=[{"role": "user", "content": "Hello"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get weather",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "location": {"type": "string"}
                            }
                        }
                    }
                }
            ],
            generation={},
            stream=False,
            raw_extra={},
            source_format="google-generative-ai",
        )

        with pytest.raises(NotImplementedError, match="function calling"):
            self.adapter.denormalize(canonical)

    def test_google_adapter_works_without_tools(self):
        """Should work normally when no tools are specified."""
        canonical = CanonicalRequest(
            model="gemini-2-flash",
            system="You are helpful.",
            messages=[{"role": "user", "content": "Hello"}],
            tools=None,
            generation={},
            stream=False,
            raw_extra={},
            source_format="google-generative-ai",
        )

        result = self.adapter.denormalize(canonical)
        payload = json.loads(result)

        assert payload["model"] == "gemini-2-flash"
        assert payload["stream"] is False
        assert "tools" not in payload

    def test_google_adapter_empty_tools_triggers_stub(self):
        """Empty tools array should NOT trigger the stub (backward compat)."""
        canonical = CanonicalRequest(
            model="gemini-2-flash",
            system="You are helpful.",
            messages=[{"role": "user", "content": "Hello"}],
            tools=[],
            generation={},
            stream=False,
            raw_extra={},
            source_format="google-generative-ai",
        )

        # Empty tools array is falsy in Python, so it should not trigger _validate_tool_support()
        result = self.adapter.denormalize(canonical)
        payload = json.loads(result)

        assert payload["model"] == "gemini-2-flash"
        assert payload["tools"] == []

    def test_validate_tool_support_error_message_includes_guidance(self):
        """Error message should include guidance on alternatives."""
        canonical = CanonicalRequest(
            model="gemini-2-flash",
            system="You are helpful.",
            messages=[{"role": "user", "content": "Hello"}],
            tools=[{"type": "function", "function": {"name": "test"}}],
            generation={},
            stream=False,
            raw_extra={},
            source_format="google-generative-ai",
        )

        with pytest.raises(NotImplementedError) as exc_info:
            self.adapter.denormalize(canonical)

        error_text = str(exc_info.value)
        assert "OpenAI" in error_text or "Anthropic" in error_text
        assert "function calling" in error_text.lower()
