"""Unit tests for budget.py — quadratic budget allocation with citation utility."""

from tokenpak.telemetry.budget import BudgetBlock, quadratic_allocate


class TestBudgetBlock:
    """Tests for BudgetBlock dataclass."""

    def test_importance_default_scores(self):
        """Default scores (0.5/0.5/1.0/0.5) should yield 6.0 importance."""
        block = BudgetBlock(
            ref="test", relevance_score=0.5, recency_score=0.5, quality_score=1.0, type_weight=0.5
        )
        # importance = (0.4*0.5 + 0.2*0.5 + 0.2*1.0 + 0.2*0.5) * 10 = 0.6 * 10 = 6.0
        assert block.importance == 6.0

    def test_importance_high_scores(self):
        """All 1.0 scores should yield maximum importance."""
        block = BudgetBlock(
            ref="test", relevance_score=1.0, recency_score=1.0, quality_score=1.0, type_weight=1.0
        )
        assert block.importance == 10.0

    def test_importance_zero_scores(self):
        """All 0.0 scores should yield zero importance."""
        block = BudgetBlock(
            ref="test", relevance_score=0.0, recency_score=0.0, quality_score=0.0, type_weight=0.0
        )
        assert block.importance == 0.0

    def test_importance_with_utility_weight(self):
        """Utility weight should modulate importance."""
        block = BudgetBlock(
            ref="test",
            relevance_score=0.5,
            recency_score=0.5,
            quality_score=1.0,
            type_weight=0.5,
            utility_weight=2.0,
        )
        # Base importance = 5.0, multiplied by utility_weight=2.0 → 10.0 (clamped to max)
        assert block.importance == 10.0

    def test_importance_with_utility_weight_below_1(self):
        """Utility weight < 1.0 should suppress importance."""
        block = BudgetBlock(
            ref="test",
            relevance_score=0.5,
            recency_score=0.5,
            quality_score=1.0,
            type_weight=0.5,
            utility_weight=0.5,
        )
        # Base importance = 6.0, multiplied by 0.5 → 3.0
        assert block.importance == 3.0

    def test_importance_clamped_to_max(self):
        """Importance should be clamped to max 10.0."""
        block = BudgetBlock(
            ref="test",
            relevance_score=1.0,
            recency_score=1.0,
            quality_score=1.0,
            type_weight=1.0,
            utility_weight=5.0,
        )
        assert block.importance == 10.0  # Clamped, not 50.0


class TestQuadraticAllocate:
    """Tests for quadratic_allocate function."""

    def test_empty_blocks(self):
        """Empty block list should return empty allocation."""
        result = quadratic_allocate([], total_budget=1000)
        assert result == {}

    def test_zero_budget(self):
        """Zero budget should return empty allocation."""
        blocks = [BudgetBlock(ref="a")]
        result = quadratic_allocate(blocks, total_budget=0)
        assert result == {}

    def test_negative_budget(self):
        """Negative budget should return empty allocation."""
        blocks = [BudgetBlock(ref="a")]
        result = quadratic_allocate(blocks, total_budget=-100)
        assert result == {}

    def test_single_block(self):
        """Single block should get entire budget."""
        blocks = [
            BudgetBlock(
                ref="a", relevance_score=0.5, recency_score=0.5, quality_score=1.0, type_weight=0.5
            )
        ]
        result = quadratic_allocate(blocks, total_budget=1000)
        assert result == {"a": 1000}

    def test_multiple_blocks_equal_importance(self):
        """Equal importance blocks should get floor + equal shares of remainder."""
        blocks = [
            BudgetBlock(
                ref="a", relevance_score=0.5, recency_score=0.5, quality_score=1.0, type_weight=0.5
            ),
            BudgetBlock(
                ref="b", relevance_score=0.5, recency_score=0.5, quality_score=1.0, type_weight=0.5
            ),
        ]
        result = quadratic_allocate(blocks, total_budget=1000)
        # floor_tokens = 1000 * 0.03 = 30 per block = 60 total
        # remaining = 1000 - 60 = 940
        # Both have importance 5.0, squared = 25.0 each, total_sq = 50.0
        # remainder split equally: 470 each
        # Final: 30 + 470 = 500 each
        assert result == {"a": 500, "b": 500}

    def test_multiple_blocks_different_importance(self):
        """Higher importance blocks should get more tokens."""
        blocks = [
            BudgetBlock(
                ref="high",
                relevance_score=1.0,
                recency_score=1.0,
                quality_score=1.0,
                type_weight=1.0,
            ),
            BudgetBlock(
                ref="low",
                relevance_score=0.0,
                recency_score=0.0,
                quality_score=0.0,
                type_weight=0.0,
            ),
        ]
        result = quadratic_allocate(blocks, total_budget=1000)
        # high importance = 10.0, squared = 100.0
        # low importance = 0.0, squared = 0.0, total_sq = 100.0
        # floor = 30 each
        # remaining = 940, all goes to 'high'
        assert result["high"] == 970
        assert result["low"] == 30

    def test_floor_ratio(self):
        """Custom floor_ratio should control minimum allocation."""
        blocks = [BudgetBlock(ref="a"), BudgetBlock(ref="b")]
        result = quadratic_allocate(blocks, total_budget=1000, floor_ratio=0.1)
        # floor_tokens = 1000 * 0.1 = 100 per block
        # Both blocks should get at least 100
        assert result["a"] >= 100
        assert result["b"] >= 100

    def test_floor_too_large(self):
        """If floor exceeds budget, should adjust to per-block minimum."""
        blocks = [
            BudgetBlock(ref="a"),
            BudgetBlock(ref="b"),
            BudgetBlock(ref="c"),
        ]
        result = quadratic_allocate(blocks, total_budget=100, floor_ratio=0.5)
        # floor_tokens = 100 * 0.5 = 50
        # 50 * 3 = 150 > 100, so floor_tokens = max(1, 100 // 3) = 33
        # 33 * 3 = 99, remainder = 1
        assert sum(result.values()) == 100
        assert all(v >= 1 for v in result.values())

    def test_rounding_remainder_distributed(self):
        """Rounding remainder should be distributed to highest-importance blocks."""
        blocks = [
            BudgetBlock(
                ref="high",
                relevance_score=1.0,
                recency_score=1.0,
                quality_score=1.0,
                type_weight=1.0,
            ),
            BudgetBlock(
                ref="med",
                relevance_score=0.5,
                recency_score=0.5,
                quality_score=1.0,
                type_weight=0.5,
            ),
            BudgetBlock(
                ref="low",
                relevance_score=0.0,
                recency_score=0.0,
                quality_score=0.0,
                type_weight=0.0,
            ),
        ]
        result = quadratic_allocate(blocks, total_budget=1000)
        # Budget should be fully consumed
        assert sum(result.values()) == 1000
        # High-importance block should get most tokens
        assert result["high"] > result["med"] > result["low"]

    def test_quadratic_weighting_emphasizes_top(self):
        """Quadratic weighting should strongly emphasize high-importance blocks."""
        blocks = [
            BudgetBlock(
                ref="a", relevance_score=1.0, recency_score=1.0, quality_score=1.0, type_weight=1.0
            ),
            BudgetBlock(
                ref="b", relevance_score=0.5, recency_score=0.5, quality_score=1.0, type_weight=0.5
            ),
            BudgetBlock(
                ref="c",
                relevance_score=0.25,
                recency_score=0.25,
                quality_score=0.5,
                type_weight=0.25,
            ),
        ]
        result = quadratic_allocate(blocks, total_budget=10000)
        # 'a' (importance 10.0, squared 100) should dominate
        # 'b' (importance 5.0, squared 25) moderate
        # 'c' (importance ~1.875, squared ~3.5) minimal
        assert result["a"] > result["b"] > result["c"]

    def test_utility_weight_affects_allocation(self):
        """Blocks with high utility_weight should get more tokens."""
        blocks = [
            BudgetBlock(
                ref="boosted",
                relevance_score=0.5,
                recency_score=0.5,
                quality_score=1.0,
                type_weight=0.5,
                utility_weight=2.0,
            ),
            BudgetBlock(
                ref="normal",
                relevance_score=0.5,
                recency_score=0.5,
                quality_score=1.0,
                type_weight=0.5,
                utility_weight=1.0,
            ),
        ]
        result = quadratic_allocate(blocks, total_budget=1000)
        # boosted has importance 10.0, normal has 5.0
        # boosted squared = 100, normal squared = 25
        assert result["boosted"] > result["normal"]

    def test_total_budget_preserved(self):
        """Allocated tokens should exactly equal total_budget."""
        blocks = [
            BudgetBlock(
                ref=f"block_{i}",
                relevance_score=0.5,
                recency_score=0.5,
                quality_score=1.0,
                type_weight=0.5,
            )
            for i in range(5)
        ]
        result = quadratic_allocate(blocks, total_budget=12345)
        assert sum(result.values()) == 12345

    def test_no_negative_allocations(self):
        """No block should receive negative allocation."""
        blocks = [
            BudgetBlock(
                ref=f"block_{i}",
                relevance_score=0.5,
                recency_score=0.5,
                quality_score=1.0,
                type_weight=0.5,
            )
            for i in range(10)
        ]
        result = quadratic_allocate(blocks, total_budget=1000)
        assert all(v >= 0 for v in result.values())

    def test_floor_ratio_zero(self):
        """floor_ratio=0 should allow blocks to get zero allocation."""
        blocks = [
            BudgetBlock(
                ref="high",
                relevance_score=1.0,
                recency_score=1.0,
                quality_score=1.0,
                type_weight=1.0,
            ),
            BudgetBlock(
                ref="low",
                relevance_score=0.0,
                recency_score=0.0,
                quality_score=0.0,
                type_weight=0.0,
            ),
        ]
        result = quadratic_allocate(blocks, total_budget=1000, floor_ratio=0.0)
        # 'low' has 0 importance, squared=0, should get nothing (no floor)
        assert result["low"] == 0
        assert result["high"] == 1000
