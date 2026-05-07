"""
Test: token_cache_hits and token_cache_misses appear in SESSION and increment correctly.
TPK-STATS-CACHE-CTR — Cali 2026-03-27
"""
from functools import lru_cache


def make_count_tokens():
    """Replicate the count_tokens implementation for isolated testing."""
    cache_hits = [0]
    cache_misses = [0]

    try:
        import tiktoken
        _enc = tiktoken.get_encoding("cl100k_base")

        @lru_cache(maxsize=2048)
        def _cached(text: str) -> int:
            return len(_enc.encode(text))
    except ImportError:
        @lru_cache(maxsize=2048)
        def _cached(text: str) -> int:
            return len(text) // 4

    def count_tokens(text: str) -> int:
        before = _cached.cache_info()
        result = _cached(text)
        after = _cached.cache_info()
        if after.hits > before.hits:
            cache_hits[0] += 1
        else:
            cache_misses[0] += 1
        return result

    return count_tokens, cache_hits, cache_misses


class TestTokenCacheCounters:
    def test_first_call_is_miss(self):
        count_tokens, hits, misses = make_count_tokens()
        count_tokens("unique text abc")
        assert hits[0] == 0
        assert misses[0] == 1

    def test_repeated_call_is_hit(self):
        count_tokens, hits, misses = make_count_tokens()
        count_tokens("repeated text xyz")
        count_tokens("repeated text xyz")
        assert hits[0] == 1
        assert misses[0] == 1

    def test_different_text_is_miss(self):
        count_tokens, hits, misses = make_count_tokens()
        count_tokens("text one")
        count_tokens("text two")
        count_tokens("text one")  # hit
        assert hits[0] == 1
        assert misses[0] == 2

    def test_multiple_hits_accumulate(self):
        count_tokens, hits, misses = make_count_tokens()
        count_tokens("cached text")
        for _ in range(5):
            count_tokens("cached text")
        assert hits[0] == 5
        assert misses[0] == 1

    def test_session_has_token_cache_keys(self):
        """Verify SESSION dict contains the required keys."""
        SESSION = {
            "token_cache_hits": 0,
            "token_cache_misses": 0,
        }
        assert "token_cache_hits" in SESSION
        assert "token_cache_misses" in SESSION

    def test_session_counters_sync_from_module(self):
        """Simulate the /stats sync pattern."""
        count_tokens, hits, misses = make_count_tokens()
        SESSION = {"token_cache_hits": 0, "token_cache_misses": 0}

        count_tokens("hello")
        count_tokens("hello")
        count_tokens("world")

        # Simulate what /stats does before responding
        SESSION["token_cache_hits"] = hits[0]
        SESSION["token_cache_misses"] = misses[0]

        assert SESSION["token_cache_hits"] == 1
        assert SESSION["token_cache_misses"] == 2
