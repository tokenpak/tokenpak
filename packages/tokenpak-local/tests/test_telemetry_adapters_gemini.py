"""Tests for the Gemini telemetry adapter."""

import pytest
from tokenpak.telemetry.adapters.gemini import GeminiAdapter, _FINISH_REASON_MAP
from tokenpak.telemetry.canonical import (
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    Confidence,
    UsageSource,
)


@pytest.fixture
def adapter():
    return GeminiAdapter()


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------

class TestGeminiDetect:
    def test_candidates_only(self, adapter):
        name, score = adapter.detect({"candidates": []})
        assert name == "gemini"
        assert score == 0.9

    def test_candidates_with_usage_metadata(self, adapter):
        name, score = adapter.detect({"candidates": [], "usageMetadata": {}})
        assert score == 1.0

    def test_contents_only(self, adapter):
        name, score = adapter.detect({"contents": []})
        assert score == 0.6

    def test_contents_with_generation_config(self, adapter):
        name, score = adapter.detect({"contents": [], "generationConfig": {"temperature": 0.5}})
        assert score == 0.75

    def test_negative_choices_signal(self, adapter):
        name, score = adapter.detect({"choices": [], "candidates": []})
        assert score == 0.0

    def test_negative_stop_reason_signal(self, adapter):
        name, score = adapter.detect({"stop_reason": "end_turn", "candidates": []})
        assert score == 0.0

    def test_empty_payload(self, adapter):
        name, score = adapter.detect({})
        assert score == 0.0


# ---------------------------------------------------------------------------
# to_canonical_request()
# ---------------------------------------------------------------------------

class TestGeminiToCanonicalRequest:
    def test_basic_request(self, adapter):
        raw = {
            "model": "gemini-1.5-pro",
            "contents": [{"role": "user", "parts": [{"text": "Hello"}]}],
        }
        req = adapter.to_canonical_request(raw)
        assert isinstance(req, CanonicalRequest)
        assert req.provider == "gemini"
        assert req.model == "gemini-1.5-pro"
        assert req.messages == [{"role": "user", "parts": [{"text": "Hello"}]}]

    def test_generation_config_in_params(self, adapter):
        raw = {
            "model": "gemini-1.5-flash",
            "contents": [],
            "generationConfig": {"temperature": 0.8, "maxOutputTokens": 500},
        }
        req = adapter.to_canonical_request(raw)
        assert req.params["generationConfig"]["temperature"] == 0.8

    def test_tools_preserved(self, adapter):
        raw = {
            "model": "gemini-1.5-pro",
            "contents": [],
            "tools": [{"functionDeclarations": [{"name": "search"}]}],
        }
        req = adapter.to_canonical_request(raw)
        assert len(req.tools) == 1

    def test_model_excluded_from_params(self, adapter):
        raw = {"model": "gemini-1.5-pro", "contents": [], "safetySettings": []}
        req = adapter.to_canonical_request(raw)
        assert "model" not in req.params

    def test_empty_contents(self, adapter):
        raw = {"model": "gemini-1.5-flash", "contents": []}
        req = adapter.to_canonical_request(raw)
        assert req.messages == []


# ---------------------------------------------------------------------------
# to_canonical_response()
# ---------------------------------------------------------------------------

class TestGeminiToCanonicalResponse:
    def test_text_response(self, adapter):
        raw = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Hello!"}], "role": "model"},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
        }
        resp = adapter.to_canonical_response(raw)
        assert isinstance(resp, CanonicalResponse)
        assert resp.output == "Hello!"
        assert resp.finish_reason == "stop"
        assert resp.error is None

    def test_multiple_text_parts_joined(self, adapter):
        raw = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Part 1"}, {"text": "Part 2"}],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ]
        }
        resp = adapter.to_canonical_response(raw)
        assert resp.output == "Part 1\nPart 2"

    def test_max_tokens_finish_reason(self, adapter):
        raw = {
            "candidates": [
                {"content": {"parts": [{"text": "..."}]}, "finishReason": "MAX_TOKENS"}
            ]
        }
        resp = adapter.to_canonical_response(raw)
        assert resp.finish_reason == "max_tokens"

    def test_safety_finish_reason(self, adapter):
        raw = {
            "candidates": [
                {"content": {"parts": []}, "finishReason": "SAFETY"}
            ]
        }
        resp = adapter.to_canonical_response(raw)
        assert resp.finish_reason == "stop"

    def test_error_response(self, adapter):
        raw = {"error": {"message": "API quota exceeded", "code": 429}}
        resp = adapter.to_canonical_response(raw)
        assert resp.finish_reason == "error"
        assert "quota" in resp.error

    def test_error_string(self, adapter):
        raw = {"error": "Unknown error"}
        resp = adapter.to_canonical_response(raw)
        assert resp.error == "Unknown error"

    def test_empty_candidates(self, adapter):
        raw = {"candidates": []}
        resp = adapter.to_canonical_response(raw)
        assert resp.finish_reason == "unknown"
        assert resp.error == "No candidates in response"

    def test_non_text_parts_preserved_as_list(self, adapter):
        raw = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"functionCall": {"name": "search", "args": {}}},
                            {"text": "Also some text"},
                        ],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ]
        }
        resp = adapter.to_canonical_response(raw)
        # Mixed parts → list preserved
        assert isinstance(resp.output, list)


# ---------------------------------------------------------------------------
# extract_usage()
# ---------------------------------------------------------------------------

class TestGeminiExtractUsage:
    def test_basic_usage(self, adapter):
        raw = {
            "usageMetadata": {
                "promptTokenCount": 100,
                "candidatesTokenCount": 50,
                "totalTokenCount": 150,
            }
        }
        usage = adapter.extract_usage(raw)
        assert usage.input_billed == 100
        assert usage.output_billed == 50
        assert usage.cache_read == 0
        assert usage.confidence == Confidence.HIGH
        assert usage.usage_source == UsageSource.PROVIDER_REPORTED

    def test_cached_content_tokens(self, adapter):
        raw = {
            "usageMetadata": {
                "promptTokenCount": 200,
                "candidatesTokenCount": 80,
                "cachedContentTokenCount": 150,
            }
        }
        usage = adapter.extract_usage(raw)
        assert usage.cache_read == 150

    def test_missing_usage_metadata(self, adapter):
        usage = adapter.extract_usage({})
        assert usage.confidence == Confidence.LOW
        assert usage.usage_source == UsageSource.UNKNOWN
        assert usage.input_billed == 0
        assert usage.output_billed == 0

    def test_none_usage_metadata(self, adapter):
        usage = adapter.extract_usage({"usageMetadata": None})
        assert usage.confidence == Confidence.LOW

    def test_zero_tokens(self, adapter):
        raw = {
            "usageMetadata": {
                "promptTokenCount": 0,
                "candidatesTokenCount": 0,
            }
        }
        usage = adapter.extract_usage(raw)
        assert usage.input_billed == 0
        assert usage.output_billed == 0
        assert usage.confidence == Confidence.HIGH

    def test_cache_write_always_zero(self, adapter):
        raw = {
            "usageMetadata": {
                "promptTokenCount": 100,
                "candidatesTokenCount": 50,
            }
        }
        usage = adapter.extract_usage(raw)
        assert usage.cache_write == 0

    def test_input_est_matches_billed(self, adapter):
        raw = {
            "usageMetadata": {
                "promptTokenCount": 120,
                "candidatesTokenCount": 60,
            }
        }
        usage = adapter.extract_usage(raw)
        assert usage.input_est == 120
        assert usage.output_est == 60
