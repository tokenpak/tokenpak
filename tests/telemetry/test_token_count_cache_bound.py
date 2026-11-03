"""The token-count cache stays small however large the counted texts are.

``count_tokens`` used to be an ``lru_cache`` keyed by the text itself: 8,192
entries of the 100,000-character chunks the proxy's token-count endpoint
counts (or of the prefixes ``truncate_to_tokens`` tries) kept hundreds of
megabytes alive. Long texts are now keyed by length and digest.
"""

from __future__ import annotations

import gc
import tracemalloc

import pytest

from tokenpak.telemetry import tokens


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    # The cache does not depend on the encoder; the chars/4 counter keeps
    # these tests offline and keeps encoder loading out of the traced memory.
    monkeypatch.setattr(tokens, "_get_encoder", lambda: None)
    tokens.clear_cache()
    yield
    tokens.clear_cache()


def test_long_texts_are_not_kept_alive_by_the_cache() -> None:
    tracemalloc.start()
    try:
        gc.collect()
        before, _ = tracemalloc.get_traced_memory()
        for i in range(40):
            text = f"{i} " + "word " * 40_000  # 200 KB each, 8 MB in all
            assert tokens.count_tokens(text) > 0
            del text
        gc.collect()
        after, _ = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert tokens.cache_info().currsize == 40
    assert after - before < 1 << 20, f"cache retained {after - before} bytes"


def test_long_texts_still_hit_the_cache_and_count_exactly() -> None:
    text = "alpha beta gamma " * 5_000
    first = tokens.count_tokens(text)
    hits = tokens.cache_info().hits
    assert tokens.count_tokens("" + text[:]) == first
    assert tokens.cache_info().hits == hits + 1
    assert first == tokens.count_tokens_uncached(text)
    assert tokens.count_tokens(text + "!") == tokens.count_tokens_uncached(text + "!")


def test_cache_is_lru_bounded_and_clearable(monkeypatch) -> None:
    monkeypatch.setattr(tokens, "_CACHE_MAXSIZE", 3)
    for word in ("one", "two", "three"):
        tokens.count_tokens(word)
    tokens.count_tokens("one")  # most recently used
    tokens.count_tokens("four")  # evicts "two"
    info = tokens.cache_info()
    assert info.currsize == 3 and info.maxsize == 3
    misses = info.misses
    tokens.count_tokens("one")
    assert tokens.cache_info().misses == misses
    tokens.count_tokens("two")
    assert tokens.cache_info().misses == misses + 1
    tokens.clear_cache()
    assert tokens.cache_info() == (0, 0, 3, 0)


def test_empty_text_counts_zero() -> None:
    assert tokens.count_tokens("") == 0
