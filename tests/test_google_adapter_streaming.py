"""Tests for Google adapter streaming detection (GAR-A2)."""

from __future__ import annotations

from tokenpak.proxy.adapters import GoogleGenerativeAIAdapter


class TestGoogleStreamingPathDetection:
    """detect_streaming returns True for streamGenerateContent in URL path."""

    def setup_method(self):
        self.adapter = GoogleGenerativeAIAdapter()

    def test_stream_generate_content_in_path(self):
        """Standard v1 path with streamGenerateContent signals streaming."""
        path = "/v1/models/gemini-pro:streamGenerateContent"
        assert self.adapter.detect_streaming(path) is True

    def test_stream_generate_content_with_api_version(self):
        """v1beta path with streamGenerateContent signals streaming."""
        path = "/v1beta/models/gemini-1.5-pro:streamGenerateContent"
        assert self.adapter.detect_streaming(path) is True

    def test_stream_generate_content_with_key_param(self):
        """streamGenerateContent path with API key query param still signals streaming."""
        path = "/v1/models/gemini-pro:streamGenerateContent?key=abc123"
        assert self.adapter.detect_streaming(path) is True


class TestGoogleStreamingQueryParamDetection:
    """detect_streaming returns True for ?alt=sse query parameter."""

    def setup_method(self):
        self.adapter = GoogleGenerativeAIAdapter()

    def test_alt_sse_query_param(self):
        """?alt=sse alone signals streaming."""
        path = "/v1beta/models/gemini-pro:generateContent?alt=sse"
        assert self.adapter.detect_streaming(path) is True

    def test_alt_sse_with_other_params(self):
        """?alt=sse alongside other query params still signals streaming."""
        path = "/v1beta/models/gemini-pro:generateContent?alt=sse&key=abc123"
        assert self.adapter.detect_streaming(path) is True


class TestGoogleNonStreamingDetection:
    """detect_streaming returns False for non-streaming paths."""

    def setup_method(self):
        self.adapter = GoogleGenerativeAIAdapter()

    def test_generate_content_path(self):
        """Plain generateContent (non-streaming) returns False."""
        path = "/v1beta/models/gemini-pro:generateContent"
        assert self.adapter.detect_streaming(path) is False

    def test_generate_content_with_key(self):
        """generateContent with API key but no streaming signals returns False."""
        path = "/v1/models/gemini-1.5-pro:generateContent?key=abc123"
        assert self.adapter.detect_streaming(path) is False

    def test_empty_path(self):
        """Empty path returns False."""
        assert self.adapter.detect_streaming("") is False

    def test_non_google_path(self):
        """Non-Google path without streaming signals returns False."""
        path = "/v1/chat/completions"
        assert self.adapter.detect_streaming(path) is False
