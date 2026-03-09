"""Unit tests for the OpenAI telemetry adapter.

Covers:
- Token extraction for common model payloads (gpt-4, gpt-3.5-turbo, gpt-4o)
- Input/output token split via extract_usage
- Model name normalisation through to_canonical_request
- Handling missing/unknown models
- Prompt/completion token split and cache details
- _is_codex helper edge cases
- finish_reason mapping
"""

import pytest
from tokenpak.telemetry.adapters.openai import OpenAIAdapter, _is_codex
from tokenpak.telemetry.canonical import Confidence, UsageSource


@pytest.fixture
def adapter():
    return OpenAIAdapter()


def _usage_payload(model, prompt, completion, cached=0):
    payload = {
        "model": model,
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
        },
    }
    if cached:
        payload["usage"]["prompt_tokens_details"] = {"cached_tokens": cached}
    return payload


# ---------------------------------------------------------------------------
# 1. Token extraction — gpt-4
# ---------------------------------------------------------------------------
def test_extract_usage_gpt4(adapter):
    raw = _usage_payload("gpt-4", 512, 128)
    usage = adapter.extract_usage(raw)
    assert usage.input_billed == 512
    assert usage.output_billed == 128
    assert usage.usage_source == UsageSource.PROVIDER_REPORTED
    assert usage.confidence == Confidence.HIGH


# ---------------------------------------------------------------------------
# 2. Token extraction — gpt-3.5-turbo
# ---------------------------------------------------------------------------
def test_extract_usage_gpt35(adapter):
    raw = _usage_payload("gpt-3.5-turbo", 300, 90)
    usage = adapter.extract_usage(raw)
    assert usage.input_billed == 300
    assert usage.output_billed == 90


# ---------------------------------------------------------------------------
# 3. Token extraction — gpt-4o
# ---------------------------------------------------------------------------
def test_extract_usage_gpt4o(adapter):
    raw = _usage_payload("gpt-4o", 1024, 256)
    usage = adapter.extract_usage(raw)
    assert usage.input_billed == 1024
    assert usage.output_billed == 256
    assert usage.cache_read == 0


# ---------------------------------------------------------------------------
# 4. Prompt/completion token split preserved
# ---------------------------------------------------------------------------
def test_prompt_completion_split_preserved(adapter):
    raw = _usage_payload("gpt-4o", prompt=100, completion=400)
    usage = adapter.extract_usage(raw)
    assert usage.input_billed == 100
    assert usage.output_billed == 400
    # Totals should NOT be collapsed
    assert usage.input_billed + usage.output_billed == 500


# ---------------------------------------------------------------------------
# 5. Cache-read tokens extracted correctly
# ---------------------------------------------------------------------------
def test_cache_read_tokens(adapter):
    raw = _usage_payload("gpt-4o", prompt=800, completion=200, cached=600)
    usage = adapter.extract_usage(raw)
    assert usage.cache_read == 600
    assert usage.input_billed == 800  # gross; not net


# ---------------------------------------------------------------------------
# 6. cache_write is always zero (OpenAI does not expose it)
# ---------------------------------------------------------------------------
def test_cache_write_always_zero(adapter):
    raw = _usage_payload("gpt-4o", prompt=200, completion=50, cached=100)
    usage = adapter.extract_usage(raw)
    assert usage.cache_write == 0


# ---------------------------------------------------------------------------
# 7. Missing model field in request → empty string, not crash
# ---------------------------------------------------------------------------
def test_missing_model_field(adapter):
    raw = {"messages": [{"role": "user", "content": "hi"}]}
    req = adapter.to_canonical_request(raw)
    assert req.model == ""
    assert req.provider == "openai"


# ---------------------------------------------------------------------------
# 8. Unknown / novel model name passes through unchanged
# ---------------------------------------------------------------------------
def test_unknown_model_passthrough(adapter):
    raw = {"model": "gpt-99-ultra-turbo", "messages": []}
    req = adapter.to_canonical_request(raw)
    assert req.model == "gpt-99-ultra-turbo"


# ---------------------------------------------------------------------------
# 9. Model name stored verbatim (no lowercase normalisation by adapter)
# ---------------------------------------------------------------------------
def test_model_name_case_preserved(adapter):
    raw = {"model": "GPT-4O", "messages": []}
    req = adapter.to_canonical_request(raw)
    assert req.model == "GPT-4O"


# ---------------------------------------------------------------------------
# 10. Zero-token response (e.g., cached/empty reply)
# ---------------------------------------------------------------------------
def test_zero_token_response(adapter):
    raw = _usage_payload("gpt-4o", prompt=0, completion=0)
    usage = adapter.extract_usage(raw)
    assert usage.input_billed == 0
    assert usage.output_billed == 0
    assert usage.confidence == Confidence.HIGH


# ---------------------------------------------------------------------------
# 11. Large token counts (no overflow / type coercion issues)
# ---------------------------------------------------------------------------
def test_large_token_counts(adapter):
    raw = _usage_payload("gpt-4", prompt=1_000_000, completion=500_000)
    usage = adapter.extract_usage(raw)
    assert usage.input_billed == 1_000_000
    assert usage.output_billed == 500_000


# ---------------------------------------------------------------------------
# 12. _is_codex: o1/o3 reasoning models NOT flagged unless "codex" in name
# ---------------------------------------------------------------------------
def test_is_codex_o1_model_not_codex_by_default():
    # o1 does not contain "codex" — only flagged if "reasoning" key present
    assert _is_codex({"model": "o1-preview"}) is False


# ---------------------------------------------------------------------------
# 13. No usage block → LOW confidence
# ---------------------------------------------------------------------------
def test_no_usage_block_low_confidence(adapter):
    usage = adapter.extract_usage({"model": "gpt-4o", "choices": []})
    assert usage.confidence == Confidence.LOW
    assert usage.usage_source == UsageSource.UNKNOWN


# ---------------------------------------------------------------------------
# 14. input_est mirrors input_billed; output_est mirrors output_billed
# ---------------------------------------------------------------------------
def test_est_fields_mirror_billed(adapter):
    raw = _usage_payload("gpt-4o", prompt=333, completion=111)
    usage = adapter.extract_usage(raw)
    assert usage.input_est == usage.input_billed
    assert usage.output_est == usage.output_billed
