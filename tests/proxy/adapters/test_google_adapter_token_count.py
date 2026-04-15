"""Unit tests for Google adapter input/total token count parsing (GAR-A1).

Covers:
- extract_input_tokens: promptTokenCount from usageMetadata
- extract_total_tokens: totalTokenCount from usageMetadata
- Heuristic fallback when usageMetadata is absent
"""

import json
import pathlib

import pytest

from tokenpak.proxy.adapters.google_adapter import GoogleGenerativeAIAdapter

FIXTURES = pathlib.Path(__file__).parent.parent.parent / "fixtures"


@pytest.fixture
def adapter():
    return GoogleGenerativeAIAdapter()


# ── Real fixture ──────────────────────────────────────────────────────────────


class TestGoogleTokenCountFromFixture:
    """Parse token counts from the real google_generate_response.json fixture."""

    def test_input_tokens_from_fixture(self, adapter):
        body = (FIXTURES / "google_generate_response.json").read_bytes()
        # Fixture has promptTokenCount: 152
        assert adapter.extract_input_tokens(body) == 152

    def test_total_tokens_from_fixture(self, adapter):
        body = (FIXTURES / "google_generate_response.json").read_bytes()
        # Fixture has totalTokenCount: 176
        assert adapter.extract_total_tokens(body) == 176


# ── extract_input_tokens ──────────────────────────────────────────────────────


class TestGoogleExtractInputTokens:
    def test_prompt_token_count_parsed(self, adapter):
        body = json.dumps(
            {"usageMetadata": {"promptTokenCount": 100, "totalTokenCount": 142}}
        ).encode()
        assert adapter.extract_input_tokens(body) == 100

    def test_prompt_token_count_without_total(self, adapter):
        body = json.dumps({"usageMetadata": {"promptTokenCount": 55}}).encode()
        assert adapter.extract_input_tokens(body) == 55

    def test_no_usage_metadata_returns_zero(self, adapter):
        """Absent usageMetadata returns 0; caller falls back to heuristic."""
        body = json.dumps({"candidates": []}).encode()
        assert adapter.extract_input_tokens(body) == 0

    def test_usage_metadata_missing_prompt_count_returns_zero(self, adapter):
        body = json.dumps({"usageMetadata": {"candidatesTokenCount": 42}}).encode()
        assert adapter.extract_input_tokens(body) == 0

    def test_invalid_body_returns_zero(self, adapter):
        assert adapter.extract_input_tokens(b"not-json") == 0


# ── extract_total_tokens ──────────────────────────────────────────────────────


class TestGoogleExtractTotalTokens:
    def test_total_token_count_parsed(self, adapter):
        body = json.dumps(
            {"usageMetadata": {"promptTokenCount": 100, "totalTokenCount": 142}}
        ).encode()
        assert adapter.extract_total_tokens(body) == 142

    def test_total_token_count_without_other_fields(self, adapter):
        body = json.dumps({"usageMetadata": {"totalTokenCount": 200}}).encode()
        assert adapter.extract_total_tokens(body) == 200

    def test_no_usage_metadata_returns_zero(self, adapter):
        body = json.dumps({"candidates": []}).encode()
        assert adapter.extract_total_tokens(body) == 0

    def test_invalid_body_returns_zero(self, adapter):
        assert adapter.extract_total_tokens(b"not-json") == 0


# ── Heuristic fallback ────────────────────────────────────────────────────────


class TestGoogleHeuristicFallback:
    def test_zero_input_tokens_signals_use_heuristic(self, adapter):
        """When usageMetadata absent, extract_input_tokens returns 0 (heuristic signal)."""
        response_body = json.dumps({"candidates": []}).encode()
        assert adapter.extract_input_tokens(response_body) == 0

        # extract_request_tokens (4 chars/token heuristic) still produces a count
        request_body = json.dumps(
            {"contents": [{"role": "user", "parts": [{"text": "Test prompt"}]}]}
        ).encode()
        _, heuristic_tokens = adapter.extract_request_tokens(request_body)
        assert heuristic_tokens > 0

    def test_heuristic_not_used_when_usage_metadata_present(self, adapter):
        """When usageMetadata present, extract_input_tokens returns exact count, not heuristic."""
        body = json.dumps(
            {"usageMetadata": {"promptTokenCount": 999, "totalTokenCount": 1000}}
        ).encode()
        assert adapter.extract_input_tokens(body) == 999
