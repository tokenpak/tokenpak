"""Unit tests for Google Generative AI proxy adapter token extraction.

Covers:
- extract_response_tokens: candidatesTokenCount from usageMetadata
- extract_input_tokens: promptTokenCount from usageMetadata (GAR-A1)
- extract_total_tokens: totalTokenCount from usageMetadata (GAR-A1)
- Heuristic fallback via extract_request_tokens when usageMetadata absent
"""

import json

import pytest

from tokenpak.proxy.adapters.google_adapter import GoogleGenerativeAIAdapter


@pytest.fixture
def adapter():
    return GoogleGenerativeAIAdapter()


def _response(candidates_count=None, prompt_count=None, total_count=None):
    usage = {}
    if candidates_count is not None:
        usage["candidatesTokenCount"] = candidates_count
    if prompt_count is not None:
        usage["promptTokenCount"] = prompt_count
    if total_count is not None:
        usage["totalTokenCount"] = total_count
    resp = {"candidates": [{"content": {"parts": [{"text": "Hello"}]}}]}
    if usage:
        resp["usageMetadata"] = usage
    return json.dumps(resp).encode()


# ── extract_response_tokens ──────────────────────────────────────────────────


class TestGoogleExtractResponseTokens:
    def test_candidates_token_count_from_usage_metadata(self, adapter):
        body = _response(candidates_count=42, prompt_count=100, total_count=142)
        assert adapter.extract_response_tokens(body) == 42

    def test_candidates_token_count_only(self, adapter):
        body = _response(candidates_count=7)
        assert adapter.extract_response_tokens(body) == 7

    def test_no_usage_metadata_returns_zero(self, adapter):
        body = json.dumps({"candidates": []}).encode()
        assert adapter.extract_response_tokens(body) == 0

    def test_invalid_body_returns_zero(self, adapter):
        assert adapter.extract_response_tokens(b"not-json") == 0


# ── extract_input_tokens (GAR-A1) ────────────────────────────────────────────


class TestGoogleExtractInputTokens:
    def test_prompt_token_count_from_usage_metadata(self, adapter):
        body = _response(candidates_count=42, prompt_count=100, total_count=142)
        assert adapter.extract_input_tokens(body) == 100

    def test_prompt_token_count_without_other_fields(self, adapter):
        body = _response(prompt_count=55)
        assert adapter.extract_input_tokens(body) == 55

    def test_no_usage_metadata_returns_zero(self, adapter):
        """When usageMetadata absent, returns 0; caller falls back to heuristic."""
        body = json.dumps({"candidates": []}).encode()
        assert adapter.extract_input_tokens(body) == 0

    def test_usage_metadata_missing_prompt_count_returns_zero(self, adapter):
        body = _response(candidates_count=42)  # usageMetadata present but no promptTokenCount
        assert adapter.extract_input_tokens(body) == 0

    def test_invalid_body_returns_zero(self, adapter):
        assert adapter.extract_input_tokens(b"not-json") == 0


# ── extract_total_tokens (GAR-A1) ────────────────────────────────────────────


class TestGoogleExtractTotalTokens:
    def test_total_token_count_from_usage_metadata(self, adapter):
        body = _response(candidates_count=42, prompt_count=100, total_count=142)
        assert adapter.extract_total_tokens(body) == 142

    def test_total_token_count_without_other_fields(self, adapter):
        body = _response(total_count=200)
        assert adapter.extract_total_tokens(body) == 200

    def test_no_usage_metadata_returns_zero(self, adapter):
        body = json.dumps({"candidates": []}).encode()
        assert adapter.extract_total_tokens(body) == 0

    def test_invalid_body_returns_zero(self, adapter):
        assert adapter.extract_total_tokens(b"not-json") == 0


# ── Heuristic fallback ───────────────────────────────────────────────────────


class TestGoogleHeuristicFallback:
    def test_extract_request_tokens_heuristic_still_works(self, adapter):
        """extract_request_tokens (4 chars/token) still functions when usageMetadata absent."""
        request_body = json.dumps(
            {"contents": [{"role": "user", "parts": [{"text": "Hello, world!"}]}]}
        ).encode()
        model, tokens = adapter.extract_request_tokens(request_body)
        assert tokens > 0  # "Hello, world!" = 13 chars → 3 tokens by heuristic

    def test_input_tokens_zero_signals_use_heuristic(self, adapter):
        """When usageMetadata absent, extract_input_tokens returns 0 (heuristic signal)."""
        response_body = json.dumps({"candidates": []}).encode()
        assert adapter.extract_input_tokens(response_body) == 0

        request_body = json.dumps(
            {"contents": [{"role": "user", "parts": [{"text": "Test prompt"}]}]}
        ).encode()
        _, heuristic_tokens = adapter.extract_request_tokens(request_body)
        assert heuristic_tokens > 0
