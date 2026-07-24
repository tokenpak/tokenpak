"""Unit tests for tokenpak/tokens.py — token counting and truncation utilities."""

from unittest.mock import patch

# Import module under test
from tokenpak import tokens as tok

# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------


class TestCountTokens:
    def test_empty_string_returns_zero(self):
        assert tok.count_tokens("") == 0

    def test_none_like_empty_handled(self):
        # Function accepts str; ensure empty string path works
        result = tok.count_tokens("")
        assert result == 0

    def test_short_english_text(self):
        # "Hello world" = 2 tokens in cl100k_base; fallback gives max(1, 11//4)=2
        result = tok.count_tokens("Hello world")
        assert result >= 1

    def test_returns_integer(self):
        assert isinstance(tok.count_tokens("test"), int)

    def test_longer_text_more_tokens(self):
        short = tok.count_tokens("hi")
        long_ = tok.count_tokens("This is a much longer sentence with many more words in it.")
        assert long_ > short

    def test_cached_result_consistent(self):
        text = "Consistency check text"
        first = tok.count_tokens(text)
        second = tok.count_tokens(text)
        assert first == second

    def test_fallback_mode_uses_char_estimate(self):
        """When tiktoken unavailable, falls back to len//4."""
        tok.clear_cache()
        with patch.object(tok, "_FALLBACK_MODE", True), patch.object(tok, "_ENC", None):
            result = tok.count_tokens("Hello world!")  # 12 chars → max(1, 12//4)=3
            assert result == max(1, len("Hello world!") // 4)
        tok.clear_cache()


# ---------------------------------------------------------------------------
# count_tokens_uncached
# ---------------------------------------------------------------------------


class TestCountTokensUncached:
    def test_empty_returns_zero(self):
        assert tok.count_tokens_uncached("") == 0

    def test_matches_cached_for_same_text(self):
        text = "Uncached match test"
        cached = tok.count_tokens(text)
        uncached = tok.count_tokens_uncached(text)
        assert cached == uncached

    def test_fallback_path(self):
        with patch.object(tok, "_FALLBACK_MODE", True), patch.object(tok, "_ENC", None):
            result = tok.count_tokens_uncached("abcdefgh")  # 8 chars → max(1,2)=2
            assert result == max(1, len("abcdefgh") // 4)


# ---------------------------------------------------------------------------
# truncate_to_tokens
# ---------------------------------------------------------------------------


class TestTruncateToTokens:
    def test_empty_string_returns_empty(self):
        text, count = tok.truncate_to_tokens("", 100)
        assert text == ""
        assert count == 0

    def test_zero_max_tokens_returns_empty(self):
        text, count = tok.truncate_to_tokens("Hello world", 0)
        assert text == ""
        assert count == 0

    def test_negative_max_tokens_returns_empty(self):
        text, count = tok.truncate_to_tokens("Hello world", -5)
        assert text == ""
        assert count == 0

    def test_short_text_within_limit_unchanged(self):
        short = "Hi"
        text, count = tok.truncate_to_tokens(short, 100)
        assert text == short
        assert count == tok.count_tokens(short)

    def test_truncated_text_within_token_limit(self):
        long_text = " ".join(["word"] * 500)  # ~500+ tokens
        text, count = tok.truncate_to_tokens(long_text, 20)
        assert count <= 20
        assert len(text) < len(long_text)

    def test_returns_tuple(self):
        result = tok.truncate_to_tokens("hello", 10)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_token_count_matches_returned_text(self):
        long_text = "The quick brown fox jumps over the lazy dog. " * 50
        text, count = tok.truncate_to_tokens(long_text, 15)
        actual = tok.count_tokens(text)
        # count may differ by ≤1 due to ellipsis handling
        assert abs(actual - count) <= 1


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    def test_empty_returns_zero(self):
        assert tok.estimate_tokens("") == 0

    def test_returns_at_least_one_for_nonempty(self):
        assert tok.estimate_tokens("x") >= 1

    def test_longer_text_larger_estimate(self):
        short = tok.estimate_tokens("hi")
        long_ = tok.estimate_tokens("This is a much longer sentence with many words.")
        assert long_ > short

    def test_returns_integer(self):
        assert isinstance(tok.estimate_tokens("test"), int)

    def test_utf8_multibyte_handled(self):
        # CJK chars are 3 bytes each — estimate should reflect byte length
        cjk = "中文测试"
        result = tok.estimate_tokens(cjk)
        expected = max(1, len(cjk.encode("utf-8")) // 4)
        assert result == expected


# ---------------------------------------------------------------------------
# cache_info / clear_cache
# ---------------------------------------------------------------------------


class TestCacheManagement:
    def test_cache_info_returns_namedtuple(self):
        info = tok.cache_info()
        assert hasattr(info, "hits")
        assert hasattr(info, "misses")
        assert hasattr(info, "maxsize")
        assert hasattr(info, "currsize")

    def test_clear_cache_resets_currsize(self):
        tok.count_tokens("warm up cache")
        tok.clear_cache()
        info = tok.cache_info()
        assert info.currsize == 0

    def test_cache_hit_increments_after_repeated_call(self):
        tok.clear_cache()
        text = "unique string for hit test xyz123"
        tok.count_tokens(text)
        before = tok.cache_info().hits
        tok.count_tokens(text)
        after = tok.cache_info().hits
        assert after == before + 1
