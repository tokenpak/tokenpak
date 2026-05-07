"""Tests for Google adapter usageMetadata token count parsing (GAR-A1)."""

from __future__ import annotations

import json
import os

from tokenpak.proxy.adapters import GoogleGenerativeAIAdapter

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture(name: str) -> bytes:
    path = os.path.join(FIXTURES_DIR, name)
    with open(path, "rb") as f:
        return f.read()


class TestGoogleTokenCountFromFixture:
    """Extract token counts from a real Google response fixture."""

    def setup_method(self):
        self.adapter = GoogleGenerativeAIAdapter()
        self.body = _load_fixture("google_generate_response.json")

    def test_extract_input_tokens_from_fixture(self):
        """promptTokenCount=152 is parsed from usageMetadata."""
        result = self.adapter.extract_input_tokens(self.body)
        assert result == 152

    def test_extract_total_tokens_from_fixture(self):
        """totalTokenCount=176 is parsed from usageMetadata."""
        result = self.adapter.extract_total_tokens(self.body)
        assert result == 176

    def test_extract_response_tokens_from_fixture(self):
        """candidatesTokenCount=24 is parsed from usageMetadata."""
        result = self.adapter.extract_response_tokens(self.body)
        assert result == 24

    def test_input_plus_output_equals_total(self):
        """promptTokenCount + candidatesTokenCount == totalTokenCount per fixture."""
        input_tok = self.adapter.extract_input_tokens(self.body)
        output_tok = self.adapter.extract_response_tokens(self.body)
        total_tok = self.adapter.extract_total_tokens(self.body)
        assert input_tok + output_tok == total_tok


class TestGoogleHeuristicFallback:
    """extract_input_tokens returns 0 when usageMetadata is absent."""

    def setup_method(self):
        self.adapter = GoogleGenerativeAIAdapter()

    def test_zero_input_tokens_when_no_usage_metadata(self):
        """Returns 0 when usageMetadata key is missing entirely."""
        body = json.dumps({
            "candidates": [
                {"content": {"parts": [{"text": "hello"}], "role": "model"}}
            ]
        }).encode()
        assert self.adapter.extract_input_tokens(body) == 0

    def test_zero_total_tokens_when_no_usage_metadata(self):
        """Returns 0 for totalTokenCount when usageMetadata is absent."""
        body = json.dumps({
            "candidates": [
                {"content": {"parts": [{"text": "hello"}], "role": "model"}}
            ]
        }).encode()
        assert self.adapter.extract_total_tokens(body) == 0

    def test_zero_input_tokens_when_usage_metadata_empty(self):
        """Returns 0 when usageMetadata is present but promptTokenCount is missing."""
        body = json.dumps({
            "candidates": [],
            "usageMetadata": {"candidatesTokenCount": 5},
        }).encode()
        assert self.adapter.extract_input_tokens(body) == 0

    def test_heuristic_not_used_when_usage_metadata_present(self):
        """When usageMetadata is present, returns real count (not 0)."""
        body = json.dumps({
            "candidates": [],
            "usageMetadata": {
                "promptTokenCount": 42,
                "totalTokenCount": 50,
                "candidatesTokenCount": 8,
            },
        }).encode()
        assert self.adapter.extract_input_tokens(body) == 42
        assert self.adapter.extract_total_tokens(body) == 50

    def test_zero_on_invalid_json(self):
        """Returns 0 gracefully when body is not valid JSON."""
        assert self.adapter.extract_input_tokens(b"not-json") == 0
        assert self.adapter.extract_total_tokens(b"not-json") == 0

    def test_zero_on_empty_body(self):
        """Returns 0 gracefully when body is empty."""
        assert self.adapter.extract_input_tokens(b"") == 0
        assert self.adapter.extract_total_tokens(b"") == 0
