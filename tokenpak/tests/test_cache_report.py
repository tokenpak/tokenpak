# SPDX-License-Identifier: Apache-2.0
"""Tests for tokenpak/cache_report.py"""

import pytest
from tokenpak.cache_report import format_cache_report


# ---------------------------------------------------------------------------
# Happy path tests
# ---------------------------------------------------------------------------

def test_basic_cache_report():
    """Standard call with all parameters returns expected structure."""
    result = format_cache_report(
        cache_read_tokens=800,
        new_input_tokens=200,
        turn_id="turn-abc",
        provider="anthropic",
        model="claude-sonnet-4-6",
    )
    assert result["hit_tokens"] == 800
    assert result["miss_tokens"] == 200
    assert result["total_tokens"] == 1000
    assert result["cache_ratio"] == 0.8
    assert result["turn_id"] == "turn-abc"
    assert result["provider"] == "anthropic"
    assert result["model"] == "claude-sonnet-4-6"


def test_all_cache_hits():
    """When all tokens are cached, ratio should be 1.0."""
    result = format_cache_report(
        cache_read_tokens=500,
        new_input_tokens=0,
        turn_id="t1",
        provider="openai",
        model="gpt-4o",
    )
    assert result["hit_tokens"] == 500
    assert result["miss_tokens"] == 0
    assert result["total_tokens"] == 500
    assert result["cache_ratio"] == 1.0


def test_no_cache_hits():
    """When no tokens are cached, ratio should be 0.0."""
    result = format_cache_report(
        cache_read_tokens=0,
        new_input_tokens=300,
        turn_id="t2",
        provider="google",
        model="gemini-pro",
    )
    assert result["hit_tokens"] == 0
    assert result["miss_tokens"] == 300
    assert result["total_tokens"] == 300
    assert result["cache_ratio"] == 0.0


def test_ratio_rounded_to_4_decimals():
    """Cache ratio should be rounded to 4 decimal places."""
    result = format_cache_report(
        cache_read_tokens=1,
        new_input_tokens=3,
    )
    # 1/4 = 0.25 exactly
    assert result["cache_ratio"] == 0.25

    result2 = format_cache_report(
        cache_read_tokens=1,
        new_input_tokens=2,
    )
    # 1/3 ≈ 0.3333
    assert result2["cache_ratio"] == round(1 / 3, 4)


def test_default_parameters():
    """All parameters have defaults; calling with no args should not raise."""
    result = format_cache_report()
    assert result["hit_tokens"] == 0
    assert result["miss_tokens"] == 0
    assert result["total_tokens"] == 0
    assert result["cache_ratio"] == 0.0
    assert result["turn_id"] == ""
    assert result["provider"] == ""
    assert result["model"] == ""


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_zero_tokens_no_division_error():
    """Empty cache (zero total tokens) should not raise ZeroDivisionError."""
    result = format_cache_report(cache_read_tokens=0, new_input_tokens=0)
    assert result["cache_ratio"] == 0.0
    assert result["total_tokens"] == 0


def test_extra_kwargs_ignored():
    """Extra keyword arguments should not raise an error."""
    result = format_cache_report(
        cache_read_tokens=100,
        new_input_tokens=100,
        unknown_field="ignored",
    )
    assert result["total_tokens"] == 200


def test_return_keys_complete():
    """Returned dict must contain all expected keys."""
    result = format_cache_report()
    expected_keys = {"turn_id", "hit_tokens", "miss_tokens", "total_tokens", "cache_ratio", "provider", "model"}
    assert expected_keys.issubset(result.keys())


def test_large_token_counts():
    """Should handle very large token counts without overflow."""
    result = format_cache_report(
        cache_read_tokens=10_000_000,
        new_input_tokens=5_000_000,
    )
    assert result["total_tokens"] == 15_000_000
    assert result["cache_ratio"] == round(10_000_000 / 15_000_000, 4)
