# SPDX-License-Identifier: Apache-2.0
"""Tests for the spend-guard model max-context-window registry and the
:func:`derive_block_threshold` pure function.

The spend guard's soft-block band derives dynamically from the selected
model's max context window — ``floor(max_context * 0.80)``. These tests
pin the contract:

* Known frontier-model context lookups return the right token count.
* Unknown models return ``None`` (caller falls back to the configured
  static threshold; the registry never silently invents a default).
* :func:`derive_block_threshold` is a pure function with documented
  behavior on edge cases (zero, negative, ratios outside (0, 1], unknown
  fallback).
"""

from __future__ import annotations

import pytest

from tokenpak.proxy.spend_guard import (
    DEFAULT_BLOCK_RATIO,
    derive_block_threshold,
    get_model_max_context,
)
from tokenpak.proxy.spend_guard._context_window import known_models


class TestKnownContextWindows:
    """The published frontier models we ship lookups for."""

    def test_claude_opus_4_7_is_200k(self):
        assert get_model_max_context("claude-opus-4-7") == 200_000

    def test_claude_sonnet_4_6_is_200k(self):
        assert get_model_max_context("claude-sonnet-4-6") == 200_000

    def test_claude_haiku_4_5_is_200k(self):
        assert get_model_max_context("claude-haiku-4-5") == 200_000

    def test_gpt_4_1_is_1m(self):
        assert get_model_max_context("gpt-4.1") == 1_047_576

    def test_gpt_4o_is_128k(self):
        assert get_model_max_context("gpt-4o") == 128_000

    def test_o1_is_200k(self):
        assert get_model_max_context("o1") == 200_000

    def test_gemini_2_5_pro_is_2m(self):
        assert get_model_max_context("gemini-2.5-pro") == 2_000_000

    def test_gemini_1_5_flash_is_1m(self):
        assert get_model_max_context("gemini-1.5-flash") == 1_000_000


class TestModelIdNormalization:
    """Lookups handle case, provider prefixes, date suffixes, and prefix
    matches the same way the proxy sees model ids."""

    def test_uppercase_input(self):
        assert get_model_max_context("Claude-Opus-4-7") == 200_000

    def test_provider_prefix_is_stripped(self):
        assert get_model_max_context("anthropic/claude-opus-4-7") == 200_000
        assert get_model_max_context("openai/gpt-4o") == 128_000

    def test_date_suffix_is_stripped(self):
        assert get_model_max_context("claude-opus-4-7-20261015") == 200_000

    def test_longest_prefix_match(self):
        # A future variant like "claude-opus-4-7-canary" should still
        # resolve to the base family's 200K window.
        assert get_model_max_context("claude-opus-4-7-canary") == 200_000

    def test_whitespace_is_trimmed(self):
        assert get_model_max_context("  gpt-4o  ") == 128_000


class TestUnknownContext:
    """Unknown models return ``None`` — the registry never silently
    invents a context window."""

    def test_unknown_model_returns_none(self):
        assert get_model_max_context("unknown-frontier-model") is None

    def test_empty_string_returns_none(self):
        assert get_model_max_context("") is None

    def test_whitespace_only_returns_none(self):
        assert get_model_max_context("   ") is None

    def test_none_input_returns_none(self):
        assert get_model_max_context(None) is None

    def test_random_legacy_model_returns_none(self):
        # Older models we don't carry context entries for.
        assert get_model_max_context("text-davinci-003") is None
        assert get_model_max_context("babbage") is None


class TestKnownModelsList:
    """Smoke check on the registry's public diagnostic surface."""

    def test_known_models_returns_sorted_list(self):
        models = known_models()
        assert isinstance(models, list)
        assert len(models) > 0
        assert models == sorted(models)
        assert "claude-opus-4-7" in models


class TestDeriveBlockThreshold:
    """:func:`derive_block_threshold` contract — a pure function."""

    def test_default_ratio_is_eighty_percent(self):
        assert DEFAULT_BLOCK_RATIO == 0.80

    def test_one_million_context_yields_eight_hundred_k(self):
        assert derive_block_threshold(1_000_000) == 800_000

    def test_two_hundred_k_context_yields_one_sixty_k(self):
        assert derive_block_threshold(200_000) == 160_000

    def test_five_hundred_k_context_yields_four_hundred_k(self):
        assert derive_block_threshold(500_000) == 400_000

    def test_two_million_context_yields_one_point_six_million(self):
        # Note: caller (decide()) caps this against hard_block_tokens.
        assert derive_block_threshold(2_000_000) == 1_600_000

    def test_custom_ratio_applies(self):
        assert derive_block_threshold(1_000_000, ratio=0.5) == 500_000
        assert derive_block_threshold(1_000_000, ratio=0.90) == 900_000

    def test_floors_to_int(self):
        # 333_333 * 0.80 = 266_666.4 → floor to 266_666
        assert derive_block_threshold(333_333) == 266_666

    def test_never_exceeds_max_context(self):
        # Even a ratio of exactly 1.0 must not exceed the model's max.
        assert derive_block_threshold(1_000_000, ratio=1.0) == 1_000_000


class TestDeriveBlockThresholdFallback:
    """Edge cases that fall through to ``fallback_tokens``."""

    def test_none_max_context_uses_fallback(self):
        assert derive_block_threshold(None, fallback_tokens=500_000) == 500_000

    def test_none_max_context_no_fallback_returns_none(self):
        assert derive_block_threshold(None) is None

    def test_zero_max_context_uses_fallback(self):
        assert derive_block_threshold(0, fallback_tokens=500_000) == 500_000

    def test_negative_max_context_uses_fallback(self):
        assert derive_block_threshold(-1, fallback_tokens=500_000) == 500_000

    def test_ratio_zero_uses_fallback(self):
        # A ratio of 0 is not meaningful — fall through.
        assert derive_block_threshold(1_000_000, ratio=0, fallback_tokens=500_000) == 500_000

    def test_ratio_above_one_uses_fallback(self):
        assert derive_block_threshold(1_000_000, ratio=1.5, fallback_tokens=500_000) == 500_000

    def test_negative_ratio_uses_fallback(self):
        assert derive_block_threshold(1_000_000, ratio=-0.1, fallback_tokens=500_000) == 500_000

    def test_non_int_max_context_uses_fallback(self):
        assert derive_block_threshold("1M", fallback_tokens=500_000) == 500_000  # type: ignore[arg-type]


class TestSelectedModelChangesThreshold:
    """Switching the selected model changes the derived block threshold."""

    @pytest.mark.parametrize("model_id, expected_ctx, expected_block", [
        ("claude-opus-4-7", 200_000, 160_000),
        ("claude-sonnet-4-6", 200_000, 160_000),
        ("gpt-4o", 128_000, 102_400),
        ("gpt-4.1", 1_047_576, 838_060),
        ("o1", 200_000, 160_000),
        ("gemini-2.5-pro", 2_000_000, 1_600_000),
        ("gemini-1.5-flash", 1_000_000, 800_000),
    ])
    def test_model_switch_updates_block_threshold(self, model_id, expected_ctx, expected_block):
        ctx = get_model_max_context(model_id)
        assert ctx == expected_ctx
        assert derive_block_threshold(ctx) == expected_block
