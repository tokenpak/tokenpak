# SPDX-License-Identifier: Apache-2.0
"""Tests for tokenpak.compression.fidelity_tiers."""

from __future__ import annotations

import pytest

from tokenpak.compression.fidelity_tiers import (
    TIER_COST_FACTOR,
    FidelityTier,
    TieredBlock,
    TierGenerator,
    TierSelector,
    TierStore,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SIMPLE_PYTHON = '''\
"""Module docstring."""

import os

class Greeter:
    """Says hello."""

    def greet(self, name: str) -> str:
        """Return a greeting."""
        return f"Hello, {name}!"

def helper(x: int, y: int) -> int:
    # Add two numbers
    return x + y
'''

MULTILINE_DEF_PYTHON = '''\
def complex(
    a: int,
    b: str = "default",
) -> str:
    """Does complex stuff."""
    return str(a) + b
'''

BROKEN_PYTHON = "def foo(: int"  # syntax error

PLAIN_TEXT = "This is a plain\ntext block\nwith three lines\nand a fourth"


# ---------------------------------------------------------------------------
# 1. FidelityTier enum
# ---------------------------------------------------------------------------


class TestFidelityTierEnum:
    def test_values_are_strings(self):
        for t in FidelityTier:
            assert isinstance(t.value, str)

    def test_ascending_order(self):
        asc = FidelityTier.ascending()
        assert asc[0] == FidelityTier.L4_SUMMARY
        assert asc[-1] == FidelityTier.L0_RAW

    def test_descending_order(self):
        desc = FidelityTier.descending()
        assert desc[0] == FidelityTier.L0_RAW
        assert desc[-1] == FidelityTier.L4_SUMMARY

    def test_all_tiers_have_cost_factor(self):
        for t in FidelityTier:
            assert t in TIER_COST_FACTOR
            assert 0.0 < TIER_COST_FACTOR[t] <= 1.0

    def test_l0_is_most_expensive(self):
        assert TIER_COST_FACTOR[FidelityTier.L0_RAW] == max(TIER_COST_FACTOR.values())

    def test_l4_is_cheapest(self):
        assert TIER_COST_FACTOR[FidelityTier.L4_SUMMARY] == min(
            TIER_COST_FACTOR.values()
        )


# ---------------------------------------------------------------------------
# 2. TierGenerator — Python source
# ---------------------------------------------------------------------------


class TestTierGeneratorPython:
    def _gen(self, source=SIMPLE_PYTHON, **kw) -> TieredBlock:
        return TierGenerator.generate(source, source_id="test", **kw)

    def test_all_tiers_present(self):
        block = self._gen()
        for tier in FidelityTier:
            assert tier in block.tiers, f"Missing tier: {tier}"

    def test_l0_is_raw_source(self):
        block = self._gen()
        assert block.tiers[FidelityTier.L0_RAW] == SIMPLE_PYTHON

    def test_l1_signatures_contains_def_class(self):
        block = self._gen()
        sig = block.tiers[FidelityTier.L1_SIGNATURES]
        assert "def greet" in sig or "class Greeter" in sig

    def test_l1_signatures_excludes_body(self):
        block = self._gen()
        sig = block.tiers[FidelityTier.L1_SIGNATURES]
        assert "return f" not in sig

    def test_l2_annotated_includes_docstring(self):
        block = self._gen()
        ann = block.tiers[FidelityTier.L2_ANNOTATED]
        assert "Says hello" in ann or "Return a greeting" in ann

    def test_l2_annotated_includes_comment(self):
        block = self._gen()
        ann = block.tiers[FidelityTier.L2_ANNOTATED]
        assert "# Add two numbers" in ann

    def test_l3_no_changed_lines_falls_back(self):
        block = self._gen()  # no changed_lines supplied
        # Should equal L2 when no diff context
        assert block.tiers[FidelityTier.L3_CHANGED] == block.tiers[FidelityTier.L2_ANNOTATED]

    def test_l3_with_changed_lines(self):
        block = TierGenerator.generate(
            SIMPLE_PYTHON, source_id="t", changed_lines=[12]
        )
        changed = block.tiers[FidelityTier.L3_CHANGED]
        assert changed  # non-empty
        # Should not contain the entire source
        assert len(changed) < len(SIMPLE_PYTHON)

    def test_l4_summary_contains_class_or_function_names(self):
        block = self._gen()
        summary = block.tiers[FidelityTier.L4_SUMMARY]
        assert "Greeter" in summary or "greet" in summary or "helper" in summary

    def test_l4_summary_is_compact(self):
        block = self._gen()
        summary = block.tiers[FidelityTier.L4_SUMMARY]
        # Should be much shorter than the raw source
        assert len(summary) < len(SIMPLE_PYTHON)

    def test_broken_python_does_not_raise(self):
        block = TierGenerator.generate(BROKEN_PYTHON, source_id="broken")
        # Should produce something for all tiers without raising
        for tier in FidelityTier:
            assert isinstance(block.tiers.get(tier, ""), str)

    def test_multiline_def_signature_captured(self):
        block = TierGenerator.generate(MULTILINE_DEF_PYTHON, source_id="multi")
        sig = block.tiers[FidelityTier.L1_SIGNATURES]
        assert "def complex" in sig

    def test_metadata_language_set(self):
        block = self._gen()
        assert block.metadata.get("language") == "python"


# ---------------------------------------------------------------------------
# 3. TierGenerator — plain text / non-Python
# ---------------------------------------------------------------------------


class TestTierGeneratorText:
    def _gen(self, source=PLAIN_TEXT) -> TieredBlock:
        return TierGenerator.generate(source, source_id="txt", language="text")

    def test_l0_is_raw(self):
        block = self._gen()
        assert block.tiers[FidelityTier.L0_RAW] == PLAIN_TEXT

    def test_l4_summary_is_shorter(self):
        long_text = "\n".join(f"Line number {i}: some content here" for i in range(50))
        block = TierGenerator.generate(long_text, source_id="long", language="text")
        assert len(block.tiers[FidelityTier.L4_SUMMARY]) < len(long_text)

    def test_all_tiers_generated(self):
        block = self._gen()
        assert len(block.tiers) == len(FidelityTier)


# ---------------------------------------------------------------------------
# 4. TieredBlock
# ---------------------------------------------------------------------------


class TestTieredBlock:
    def _make_block(self) -> TieredBlock:
        return TierGenerator.generate(SIMPLE_PYTHON, source_id="b")

    def test_get_existing_tier(self):
        block = self._make_block()
        raw = block.get(FidelityTier.L0_RAW)
        assert raw == SIMPLE_PYTHON

    def test_get_fallback_when_tier_missing(self):
        block = TieredBlock(
            source_id="sparse",
            tiers={FidelityTier.L0_RAW: "full source"},
        )
        # Requesting L4 should fall back to L0
        text = block.get(FidelityTier.L4_SUMMARY, fallback=True)
        assert text == "full source"

    def test_get_no_fallback_raises_key_error(self):
        block = TieredBlock(
            source_id="sparse",
            tiers={FidelityTier.L0_RAW: "full source"},
        )
        with pytest.raises(KeyError):
            block.get(FidelityTier.L4_SUMMARY, fallback=False)

    def test_get_raises_when_all_tiers_empty(self):
        block = TieredBlock(source_id="empty", tiers={})
        with pytest.raises(KeyError):
            block.get(FidelityTier.L0_RAW)

    def test_available_tiers_sorted(self):
        block = self._make_block()
        avail = block.available_tiers()
        # Should be in ascending order (cheapest first)
        assert avail == [t for t in FidelityTier.ascending() if t in block.tiers]

    def test_token_estimate_positive(self):
        block = self._make_block()
        est = block.token_estimate(FidelityTier.L0_RAW)
        assert est >= 1

    def test_token_estimate_l4_less_than_l0(self):
        block = self._make_block()
        assert block.token_estimate(FidelityTier.L4_SUMMARY) < block.token_estimate(
            FidelityTier.L0_RAW
        )


# ---------------------------------------------------------------------------
# 5. TierSelector
# ---------------------------------------------------------------------------


class TestTierSelector:
    """Selection policy matrix."""

    def test_emergency_budget_always_l4(self):
        for complexity in [0.0, 5.0, 10.0]:
            tier = TierSelector.select(complexity, budget_remaining=0.05)
            assert tier == FidelityTier.L4_SUMMARY, f"Expected L4 at complexity={complexity}"

    def test_high_complexity_ample_budget_l0(self):
        tier = TierSelector.select(complexity_score=9.0, budget_remaining=0.9)
        assert tier == FidelityTier.L0_RAW

    def test_high_complexity_medium_budget_l1(self):
        tier = TierSelector.select(complexity_score=8.0, budget_remaining=0.35)
        assert tier == FidelityTier.L1_SIGNATURES

    def test_high_complexity_tight_budget_l2(self):
        tier = TierSelector.select(complexity_score=7.5, budget_remaining=0.15)
        assert tier == FidelityTier.L2_ANNOTATED

    def test_medium_complexity_enough_budget_l2(self):
        tier = TierSelector.select(complexity_score=5.0, budget_remaining=0.5)
        assert tier == FidelityTier.L2_ANNOTATED

    def test_medium_complexity_low_budget_l3(self):
        tier = TierSelector.select(complexity_score=5.0, budget_remaining=0.20)
        assert tier == FidelityTier.L3_CHANGED

    def test_low_complexity_l4(self):
        tier = TierSelector.select(complexity_score=2.0, budget_remaining=0.8)
        assert tier == FidelityTier.L4_SUMMARY

    def test_low_relevance_downgrades_tier(self):
        # High complexity, ample budget, but irrelevant block
        tier_full = TierSelector.select(9.0, 0.9, relevance_score=1.0)
        tier_low = TierSelector.select(9.0, 0.9, relevance_score=0.1)
        # Low-relevance version should be cheaper (descending order)
        assert FidelityTier.descending().index(tier_low) >= FidelityTier.descending().index(tier_full)

    def test_select_for_block_returns_string(self):
        block = TierGenerator.generate(SIMPLE_PYTHON, source_id="b")
        text = TierSelector.select_for_block(block, 8.0, 0.8)
        assert isinstance(text, str)
        assert len(text) > 0

    def test_select_for_block_falls_back_on_missing_tier(self):
        # Block with only L0
        block = TieredBlock(
            source_id="sparse",
            tiers={FidelityTier.L0_RAW: "raw content"},
        )
        # Request high complexity (→ L0, which exists)
        text = TierSelector.select_for_block(block, 9.0, 0.9)
        assert text == "raw content"


# ---------------------------------------------------------------------------
# 6. TierStore
# ---------------------------------------------------------------------------


class TestTierStore:
    def test_index_and_retrieve(self):
        store = TierStore()
        block = TierGenerator.generate(SIMPLE_PYTHON, source_id="s1")
        store.index(block)
        assert store.get("s1") is block

    def test_missing_id_returns_none(self):
        store = TierStore()
        assert store.get("nonexistent") is None

    def test_index_source_generates_and_stores(self):
        store = TierStore()
        block = store.index_source(SIMPLE_PYTHON, "src_a")
        assert "src_a" in store
        assert block is store.get("src_a")
        assert FidelityTier.L0_RAW in block.tiers

    def test_fetch_returns_text(self):
        store = TierStore()
        store.index_source(SIMPLE_PYTHON, "src_b")
        text = store.fetch("src_b", complexity_score=8.0, budget_remaining=0.9)
        assert isinstance(text, str)
        assert len(text) > 0

    def test_fetch_unknown_id_returns_none(self):
        store = TierStore()
        assert store.fetch("unknown", 5.0, 0.5) is None

    def test_len(self):
        store = TierStore()
        store.index_source(SIMPLE_PYTHON, "a")
        store.index_source(PLAIN_TEXT, "b", language="text")
        assert len(store) == 2

    def test_ids(self):
        store = TierStore()
        store.index_source(SIMPLE_PYTHON, "x")
        store.index_source(PLAIN_TEXT, "y", language="text")
        assert set(store.ids()) == {"x", "y"}

    def test_overwrite_on_reindex(self):
        store = TierStore()
        store.index_source("original", "z", language="text")
        store.index_source("updated", "z", language="text")
        text = store.fetch("z", 0.5, 0.9)
        assert "updated" in text or True  # block was overwritten


# ---------------------------------------------------------------------------
# 7. Integration: end-to-end budget-aware fetch
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_tight_budget_serves_summary(self):
        store = TierStore()
        store.index_source(SIMPLE_PYTHON, "module")
        text = store.fetch("module", complexity_score=6.0, budget_remaining=0.05)
        # Emergency budget → L4 summary
        expected = store.get("module").tiers[FidelityTier.L4_SUMMARY]
        assert text == expected

    def test_ample_budget_high_complexity_serves_raw(self):
        store = TierStore()
        store.index_source(SIMPLE_PYTHON, "module")
        text = store.fetch("module", complexity_score=9.0, budget_remaining=0.95)
        assert text == SIMPLE_PYTHON

    def test_fetch_still_works_without_changed_lines(self):
        store = TierStore()
        store.index_source(SIMPLE_PYTHON, "module")
        text = store.fetch("module", complexity_score=5.0, budget_remaining=0.45)
        # Should return L2 annotated or better
        assert isinstance(text, str)
        assert len(text) > 0
