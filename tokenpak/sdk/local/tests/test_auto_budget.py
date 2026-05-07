"""Tests for auto_budget module."""

import pytest
from tokenpak_local.auto_budget import (
    DEFAULT_OUTPUT_FRACTION,
    auto_budget,
    budget_info,
    get_context_length,
)


class TestGetContextLength:
    def test_exact_match_llama3(self):
        assert get_context_length("llama3") == 8192

    def test_exact_match_case_insensitive(self):
        assert get_context_length("LLAMA3") == 8192
        assert get_context_length("Llama3") == 8192

    def test_exact_match_phi3(self):
        assert get_context_length("phi3") == 4096

    def test_exact_match_mistral(self):
        assert get_context_length("mistral") == 32768

    def test_exact_match_llama31(self):
        assert get_context_length("llama3.1") == 131072

    def test_prefix_match_llama3_tag(self):
        # "llama3:8b" → exact match
        assert get_context_length("llama3:8b") == 8192

    def test_prefix_match_partial(self):
        # "llama3-custom" → prefix matches "llama3"
        result = get_context_length("llama3-custom")
        assert result == 8192

    def test_prefix_longer_wins(self):
        # "llama3.1:8b" → exact, then llama3.1 prefix beats llama3
        assert get_context_length("llama3.1:8b") == 131072

    def test_unknown_model_fallback(self):
        assert get_context_length("totally-unknown-model-xyz") == 4096

    def test_unknown_model_custom_fallback(self):
        assert get_context_length("unknown", fallback=2048) == 2048

    def test_qwen2_family(self):
        assert get_context_length("qwen2.5:7b") == 131072
        assert get_context_length("qwen2.5:0.5b") == 32768

    def test_gemma2(self):
        assert get_context_length("gemma2:9b") == 8192

    def test_command_r(self):
        assert get_context_length("command-r") == 131072


class TestAutoBudget:
    def test_llama3_default(self):
        # 75% of 8192
        assert auto_budget("llama3") == 6144

    def test_phi3_default(self):
        # 75% of 4096
        assert auto_budget("phi3") == 3072

    def test_llama31_default(self):
        # 75% of 131072
        assert auto_budget("llama3.1") == 98304

    def test_custom_output_fraction(self):
        # 50% of 8192
        assert auto_budget("llama3", output_fraction=0.5) == 4096

    def test_no_output_fraction(self):
        # 100% input budget
        assert auto_budget("llama3", output_fraction=0.0) == 8192

    def test_context_length_override(self):
        # Override to 16384, keep default fraction
        assert auto_budget("llama3", context_length=16384) == 12288

    def test_invalid_output_fraction_high(self):
        with pytest.raises(ValueError):
            auto_budget("llama3", output_fraction=1.5)

    def test_invalid_output_fraction_low(self):
        with pytest.raises(ValueError):
            auto_budget("llama3", output_fraction=-0.1)

    def test_minimum_enforced(self):
        # Even if budget would be tiny
        result = auto_budget("llama3", output_fraction=0.99, minimum=512)
        assert result >= 512

    def test_default_output_fraction_constant(self):
        assert DEFAULT_OUTPUT_FRACTION == 0.25

    def test_unknown_model_fallback_budget(self):
        # Unknown → 4096, 75% = 3072
        assert auto_budget("mystery-model-abc") == 3072


class TestBudgetInfo:
    def test_returns_dict(self):
        info = budget_info("llama3")
        assert isinstance(info, dict)

    def test_expected_keys(self):
        info = budget_info("llama3")
        assert "model" in info
        assert "context_length" in info
        assert "output_fraction" in info
        assert "input_budget" in info
        assert "output_reserved" in info

    def test_sums_to_context_length(self):
        info = budget_info("llama3")
        assert info["input_budget"] + info["output_reserved"] == info["context_length"]

    def test_model_name_preserved(self):
        info = budget_info("phi3")
        assert info["model"] == "phi3"

    def test_custom_fraction(self):
        info = budget_info("llama3", output_fraction=0.5)
        assert info["output_fraction"] == 0.5
        assert info["input_budget"] == 4096
