# SPDX-License-Identifier: Apache-2.0
"""Unit tests for tokenpak.runtime.cache_telemetry — CacheTelemetry and ProviderCacheStats."""

import threading
import pytest
from tokenpak.runtime.cache_telemetry import CacheTelemetry, ProviderCacheStats


# ---------------------------------------------------------------------------
# ProviderCacheStats
# ---------------------------------------------------------------------------

class TestProviderCacheStats:
    def test_defaults(self):
        s = ProviderCacheStats()
        assert s.hits == 0
        assert s.misses == 0
        assert s.cache_read_tokens == 0
        assert s.cache_creation_tokens == 0
        assert s.savings_usd == 0.0
        assert s.mode_counts == {}

    def test_total_property(self):
        s = ProviderCacheStats(hits=3, misses=7)
        assert s.total == 10

    def test_hit_rate_property(self):
        s = ProviderCacheStats(hits=8, misses=2)
        assert s.hit_rate == pytest.approx(0.8)

    def test_hit_rate_zero_total(self):
        s = ProviderCacheStats()
        assert s.hit_rate == 0.0

    def test_to_dict_keys(self):
        s = ProviderCacheStats(hits=5, misses=3, cache_read_tokens=100, savings_usd=0.001)
        d = s.to_dict()
        assert d["hits"] == 5
        assert d["misses"] == 3
        assert d["total"] == 8
        assert d["hit_rate"] == pytest.approx(0.625)
        assert d["cache_read_tokens"] == 100
        assert "estimated_savings_usd" in d
        assert "mode_counts" in d

    def test_to_dict_hit_rate_rounded(self):
        s = ProviderCacheStats(hits=1, misses=3)
        d = s.to_dict()
        # 1/4 = 0.25 exactly
        assert d["hit_rate"] == 0.25

    def test_to_dict_savings_rounded(self):
        s = ProviderCacheStats(savings_usd=0.1234567890)
        d = s.to_dict()
        # Rounded to 6 decimal places
        assert d["estimated_savings_usd"] == pytest.approx(0.123457, rel=1e-4)


# ---------------------------------------------------------------------------
# CacheTelemetry — record()
# ---------------------------------------------------------------------------

class TestCacheTelemetryRecord:
    def test_record_hit(self):
        t = CacheTelemetry()
        t.record("anthropic", "block_explicit", cache_read_tokens=500, cache_creation_tokens=0)
        d = t.to_dict()
        assert d["by_provider"]["anthropic"]["hits"] == 1
        assert d["by_provider"]["anthropic"]["misses"] == 0
        assert d["by_provider"]["anthropic"]["cache_read_tokens"] == 500

    def test_record_miss(self):
        t = CacheTelemetry()
        t.record("openai", "prefix_auto", cache_read_tokens=0, cache_creation_tokens=200)
        d = t.to_dict()
        assert d["by_provider"]["openai"]["misses"] == 1
        assert d["by_provider"]["openai"]["hits"] == 0
        assert d["by_provider"]["openai"]["cache_creation_tokens"] == 200

    def test_record_accumulates(self):
        t = CacheTelemetry()
        t.record("anthropic", "block_explicit", cache_read_tokens=100, cache_creation_tokens=0)
        t.record("anthropic", "block_explicit", cache_read_tokens=200, cache_creation_tokens=50)
        d = t.to_dict()
        ap = d["by_provider"]["anthropic"]
        assert ap["hits"] == 2
        assert ap["cache_read_tokens"] == 300
        assert ap["cache_creation_tokens"] == 50

    def test_record_mode_counts(self):
        t = CacheTelemetry()
        t.record("anthropic", "block_explicit", cache_read_tokens=100, cache_creation_tokens=0)
        t.record("anthropic", "block_explicit", cache_read_tokens=100, cache_creation_tokens=0)
        t.record("anthropic", "prefix_auto", cache_read_tokens=50, cache_creation_tokens=0)
        d = t.to_dict()
        mc = d["by_provider"]["anthropic"]["mode_counts"]
        assert mc["block_explicit"] == 2
        assert mc["prefix_auto"] == 1

    def test_record_mode_none_not_counted(self):
        t = CacheTelemetry()
        t.record("openai", None, cache_read_tokens=0, cache_creation_tokens=0)
        d = t.to_dict()
        assert d["by_provider"]["openai"]["mode_counts"] == {}

    def test_record_savings_accumulated(self):
        t = CacheTelemetry()
        t.record("anthropic", "block_explicit", cache_read_tokens=100, cache_creation_tokens=0, savings_usd=0.01)
        t.record("anthropic", "block_explicit", cache_read_tokens=200, cache_creation_tokens=0, savings_usd=0.02)
        d = t.to_dict()
        assert d["by_provider"]["anthropic"]["estimated_savings_usd"] == pytest.approx(0.03, rel=1e-4)

    def test_record_multiple_providers(self):
        t = CacheTelemetry()
        t.record("anthropic", "block_explicit", cache_read_tokens=100, cache_creation_tokens=0)
        t.record("openai", "prefix_auto", cache_read_tokens=0, cache_creation_tokens=50)
        d = t.to_dict()
        assert "anthropic" in d["by_provider"]
        assert "openai" in d["by_provider"]
        assert d["totals"]["total"] == 2


# ---------------------------------------------------------------------------
# CacheTelemetry — to_dict() totals
# ---------------------------------------------------------------------------

class TestCacheTelemetryToDict:
    def test_empty_totals(self):
        t = CacheTelemetry()
        d = t.to_dict()
        assert d["totals"]["hits"] == 0
        assert d["totals"]["misses"] == 0
        assert d["totals"]["total"] == 0
        assert d["totals"]["hit_rate"] == 0.0
        assert d["totals"]["estimated_savings_usd"] == 0.0
        assert d["active_providers"] == []

    def test_totals_aggregation(self):
        t = CacheTelemetry()
        t.record("anthropic", "block_explicit", cache_read_tokens=100, cache_creation_tokens=0, savings_usd=0.01)
        t.record("openai", "prefix_auto", cache_read_tokens=0, cache_creation_tokens=50)
        d = t.to_dict()
        assert d["totals"]["hits"] == 1
        assert d["totals"]["misses"] == 1
        assert d["totals"]["total"] == 2
        assert d["totals"]["hit_rate"] == pytest.approx(0.5)

    def test_active_providers_sorted(self):
        t = CacheTelemetry()
        t.record("openai", None, cache_read_tokens=0, cache_creation_tokens=0)
        t.record("anthropic", None, cache_read_tokens=10, cache_creation_tokens=0)
        d = t.to_dict()
        assert d["active_providers"] == ["anthropic", "openai"]

    def test_generated_at_present(self):
        t = CacheTelemetry()
        d = t.to_dict()
        assert "generated_at" in d
        assert "T" in d["generated_at"]  # ISO 8601 format


# ---------------------------------------------------------------------------
# CacheTelemetry — reset()
# ---------------------------------------------------------------------------

class TestCacheTelemetryReset:
    def test_reset_clears_all_data(self):
        t = CacheTelemetry()
        t.record("anthropic", "block_explicit", cache_read_tokens=100, cache_creation_tokens=0)
        t.reset()
        d = t.to_dict()
        assert d["by_provider"] == {}
        assert d["totals"]["total"] == 0

    def test_record_after_reset(self):
        t = CacheTelemetry()
        t.record("anthropic", "block_explicit", cache_read_tokens=100, cache_creation_tokens=0)
        t.reset()
        t.record("openai", "prefix_auto", cache_read_tokens=50, cache_creation_tokens=0)
        d = t.to_dict()
        assert "anthropic" not in d["by_provider"]
        assert "openai" in d["by_provider"]


# ---------------------------------------------------------------------------
# CacheTelemetry — static signal extractors
# ---------------------------------------------------------------------------

class TestExtractAnthropicSignals:
    def test_normal_response(self):
        body = {
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 50,
                "cache_read_input_tokens": 800,
                "cache_creation_input_tokens": 200,
            }
        }
        read, creation = CacheTelemetry.extract_anthropic_signals(body)
        assert read == 800
        assert creation == 200

    def test_missing_usage_key(self):
        assert CacheTelemetry.extract_anthropic_signals({}) == (0, 0)

    def test_none_values_default_to_zero(self):
        body = {"usage": {"cache_read_input_tokens": None, "cache_creation_input_tokens": None}}
        read, creation = CacheTelemetry.extract_anthropic_signals(body)
        assert read == 0
        assert creation == 0

    def test_non_dict_usage(self):
        assert CacheTelemetry.extract_anthropic_signals({"usage": "broken"}) == (0, 0)

    def test_partial_fields(self):
        body = {"usage": {"cache_read_input_tokens": 500}}
        read, creation = CacheTelemetry.extract_anthropic_signals(body)
        assert read == 500
        assert creation == 0


class TestExtractOpenAISignals:
    def test_normal_response(self):
        body = {
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 50,
                "prompt_tokens_details": {
                    "cached_tokens": 800,
                    "audio_tokens": 0,
                },
            }
        }
        read, creation = CacheTelemetry.extract_openai_signals(body)
        assert read == 800
        assert creation == 0  # OpenAI doesn't report creation

    def test_missing_usage(self):
        assert CacheTelemetry.extract_openai_signals({}) == (0, 0)

    def test_missing_prompt_tokens_details(self):
        body = {"usage": {"prompt_tokens": 100}}
        assert CacheTelemetry.extract_openai_signals(body) == (0, 0)

    def test_non_dict_usage(self):
        assert CacheTelemetry.extract_openai_signals({"usage": "broken"}) == (0, 0)

    def test_non_dict_details(self):
        body = {"usage": {"prompt_tokens_details": "broken"}}
        assert CacheTelemetry.extract_openai_signals(body) == (0, 0)

    def test_none_cached_tokens(self):
        body = {"usage": {"prompt_tokens_details": {"cached_tokens": None}}}
        read, _ = CacheTelemetry.extract_openai_signals(body)
        assert read == 0


class TestExtractGeminiSignals:
    def test_normal_response(self):
        body = {
            "usageMetadata": {
                "promptTokenCount": 1000,
                "candidatesTokenCount": 50,
                "cachedContentTokenCount": 800,
            }
        }
        read, creation = CacheTelemetry.extract_gemini_signals(body)
        assert read == 800
        assert creation == 0

    def test_missing_usage_metadata(self):
        assert CacheTelemetry.extract_gemini_signals({}) == (0, 0)

    def test_non_dict_usage_metadata(self):
        assert CacheTelemetry.extract_gemini_signals({"usageMetadata": "broken"}) == (0, 0)

    def test_none_cached_count(self):
        body = {"usageMetadata": {"cachedContentTokenCount": None}}
        read, _ = CacheTelemetry.extract_gemini_signals(body)
        assert read == 0


class TestExtractBedrockSignals:
    def test_normal_response(self):
        body = {
            "usage": {
                "inputTokens": 1000,
                "outputTokens": 50,
                "cacheReadInputTokens": 800,
                "cacheWriteInputTokens": 200,
            }
        }
        read, creation = CacheTelemetry.extract_bedrock_signals(body)
        assert read == 800
        assert creation == 200

    def test_alternate_field_names(self):
        body = {
            "usage": {
                "cacheReadInputTokenCount": 700,
                "cacheWriteInputTokenCount": 100,
            }
        }
        read, creation = CacheTelemetry.extract_bedrock_signals(body)
        assert read == 700
        assert creation == 100

    def test_missing_usage(self):
        assert CacheTelemetry.extract_bedrock_signals({}) == (0, 0)

    def test_non_dict_usage(self):
        assert CacheTelemetry.extract_bedrock_signals({"usage": "broken"}) == (0, 0)


class TestExtractSignalsFromHeaders:
    def test_dict_with_exact_case(self):
        headers = {
            "anthropic-cache-read-input-tokens": "800",
            "anthropic-cache-creation-input-tokens": "200",
        }
        read, creation = CacheTelemetry.extract_signals_from_headers(headers)
        assert read == 800
        assert creation == 200

    def test_missing_headers_default_zero(self):
        read, creation = CacheTelemetry.extract_signals_from_headers({})
        assert read == 0
        assert creation == 0

    def test_non_numeric_value_defaults_to_zero(self):
        headers = {"anthropic-cache-read-input-tokens": "not-a-number"}
        read, creation = CacheTelemetry.extract_signals_from_headers(headers)
        assert read == 0
        assert creation == 0

    def test_integer_values(self):
        headers = {
            "anthropic-cache-read-input-tokens": 500,
            "anthropic-cache-creation-input-tokens": 100,
        }
        read, creation = CacheTelemetry.extract_signals_from_headers(headers)
        assert read == 500
        assert creation == 100


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestCacheTelemetryThreadSafety:
    def test_concurrent_records(self):
        t = CacheTelemetry()
        n_threads = 10
        records_per_thread = 100

        def worker():
            for _ in range(records_per_thread):
                t.record("anthropic", "block_explicit", cache_read_tokens=1, cache_creation_tokens=0)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        d = t.to_dict()
        expected_total = n_threads * records_per_thread
        assert d["by_provider"]["anthropic"]["hits"] == expected_total
