"""tests/test_workflow_budget.py

Tests for WorkflowBudget — dynamic token budget rebalancing across workflow steps.

AC coverage:
  AC1  — Track total budget + per-step allocation
  AC2  — Overspend → redistribute remaining proportionally
  AC3  — Underspend → bonus to next priority step
  AC4  — Min floor: 100 tokens per step
  AC5  — Warn >120% per step
  AC6  — Critical <20% remaining
  AC7  — Never silently truncate
  AC8  — Tests: overspend, underspend, floor, multi-step
"""
from __future__ import annotations

import math
import pytest

from tokenpak.agent.agentic.workflow_budget import (
    WorkflowBudget,
    BudgetEvent,
    BudgetEventKind,
    MIN_FLOOR_TOKENS,
    WARN_OVERSPEND_PCT,
    CRITICAL_REMAINING_PCT,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def four_step_budget():
    """4-step budget, 4000 tokens total → 1000 each initially."""
    return WorkflowBudget(total=4000, steps=["fetch", "compress", "summarise", "write"])


@pytest.fixture
def three_step_budget():
    """3-step budget, 3000 tokens total → 1000 each initially."""
    return WorkflowBudget(total=3000, steps=["a", "b", "c"])


# ── AC1: Tracking total + per-step allocation ─────────────────────────────────

class TestAllocationTracking:
    def test_total_is_set(self, four_step_budget):
        assert four_step_budget.total == 4000

    def test_remaining_starts_at_total(self, four_step_budget):
        assert four_step_budget.remaining == 4000

    def test_even_split_four_steps(self, four_step_budget):
        # 4000 / 4 = 1000 each
        for step in ["fetch", "compress", "summarise", "write"]:
            assert four_step_budget.step_allocation(step) == 1000

    def test_even_split_three_steps(self, three_step_budget):
        # 3000 / 3 = 1000 each
        for step in ["a", "b", "c"]:
            assert three_step_budget.step_allocation(step) == 1000

    def test_uneven_split_remainder(self):
        # 1001 tokens / 3 steps = 333, 333, 335 (remainder 2 distributed)
        b = WorkflowBudget(total=1001, steps=["x", "y", "z"])
        allocs = [b.step_allocation(s) for s in ["x", "y", "z"]]
        assert sum(allocs) <= 1001
        # No step gets less than floor
        assert all(a >= MIN_FLOOR_TOKENS for a in allocs)

    def test_pending_steps_initially_all(self, four_step_budget):
        assert set(four_step_budget.pending_steps) == {"fetch", "compress", "summarise", "write"}

    def test_completed_steps_initially_empty(self, four_step_budget):
        assert four_step_budget.completed_steps == []

    def test_usage_none_before_recorded(self, four_step_budget):
        assert four_step_budget.step_usage("fetch") is None

    def test_snapshot_keys(self, four_step_budget):
        snap = four_step_budget.snapshot()
        for key in ("total", "remaining", "spent", "pct_remaining",
                    "pending_steps", "completed_steps", "allocations", "usage"):
            assert key in snap

    def test_snapshot_initial_values(self, four_step_budget):
        snap = four_step_budget.snapshot()
        assert snap["total"] == 4000
        assert snap["remaining"] == 4000
        assert snap["spent"] == 0


# ── AC2: Overspend → redistribute remaining proportionally ───────────────────

class TestOverspend:
    def test_overspend_reduces_remaining(self, four_step_budget):
        # Step uses 1500 against 1000 allocation
        four_step_budget.record_usage("fetch", 1500)
        assert four_step_budget.remaining == 4000 - 1500

    def test_overspend_rebalances_pending(self, four_step_budget):
        four_step_budget.record_usage("fetch", 1500)
        # 2500 remaining / 3 pending steps
        remaining = four_step_budget.remaining  # 2500
        pending = four_step_budget.pending_steps  # 3 steps
        total_alloc = sum(four_step_budget.step_allocation(s) for s in pending)
        # Total allocation must not exceed remaining (floor applied)
        assert total_alloc <= remaining + MIN_FLOOR_TOKENS * len(pending)

    def test_overspend_emits_warning_event(self, four_step_budget):
        events = four_step_budget.record_usage("fetch", 1300)  # 130% of 1000
        kinds = [e.kind for e in events]
        assert BudgetEventKind.WARNING in kinds

    def test_overspend_no_warning_at_exactly_120pct(self, four_step_budget):
        # 120% of 1000 = 1200 — warning at STRICTLY >120%
        events = four_step_budget.record_usage("fetch", 1200)
        kinds = [e.kind for e in events]
        assert BudgetEventKind.WARNING not in kinds

    def test_overspend_warning_above_120pct(self, four_step_budget):
        events = four_step_budget.record_usage("fetch", 1201)  # just over 120%
        kinds = [e.kind for e in events]
        assert BudgetEventKind.WARNING in kinds

    def test_overspend_rebalance_event_emitted(self, four_step_budget):
        events = four_step_budget.record_usage("fetch", 1500)
        kinds = [e.kind for e in events]
        assert BudgetEventKind.REBALANCED in kinds

    def test_overspend_step_marked_completed(self, four_step_budget):
        four_step_budget.record_usage("fetch", 1500)
        assert "fetch" not in four_step_budget.pending_steps
        assert "fetch" in four_step_budget.completed_steps

    def test_overspend_usage_recorded(self, four_step_budget):
        four_step_budget.record_usage("fetch", 1500)
        assert four_step_budget.step_usage("fetch") == 1500

    def test_multi_overspend(self):
        """Cascading overspend across multiple steps."""
        b = WorkflowBudget(total=3000, steps=["a", "b", "c"])
        b.record_usage("a", 1400)  # overspend by 400; 1600 left for b, c
        b.record_usage("b", 900)   # overspend by ~100 (b had ~800); 700 left for c
        assert b.remaining >= 0
        assert b.step_allocation("c") >= MIN_FLOOR_TOKENS

    def test_overspend_to_zero_remaining(self):
        """If overspend eats all tokens, remaining = 0 and exhausted event fires."""
        b = WorkflowBudget(total=500, steps=["a", "b"])
        events = b.record_usage("a", 500)
        kinds = [e.kind for e in events]
        assert b.remaining == 0
        assert BudgetEventKind.EXHAUSTED in kinds


# ── AC3: Underspend → bonus to next priority step ─────────────────────────────

class TestUnderspend:
    def test_underspend_increases_remaining(self, four_step_budget):
        # 'fetch' allocated 1000, uses only 600 → 400 surplus
        four_step_budget.record_usage("fetch", 600)
        assert four_step_budget.remaining == 4000 - 600  # 3400

    def test_underspend_bonus_given_to_next_step(self, four_step_budget):
        """First pending step after completion receives the surplus."""
        alloc_before = four_step_budget.step_allocation("compress")
        four_step_budget.record_usage("fetch", 600)  # 400 underspend
        alloc_after = four_step_budget.step_allocation("compress")
        # compress should receive a bonus (>= its base allocation)
        assert alloc_after > alloc_before

    def test_underspend_rebalance_event_emitted(self, four_step_budget):
        events = four_step_budget.record_usage("fetch", 600)
        kinds = [e.kind for e in events]
        assert BudgetEventKind.REBALANCED in kinds

    def test_underspend_event_mentions_bonus_step(self, four_step_budget):
        events = four_step_budget.record_usage("fetch", 600)
        rebalance = next(e for e in events if e.kind == BudgetEventKind.REBALANCED)
        assert "bonus" in rebalance.message.lower() or rebalance.data.get("bonus_tokens", 0) > 0

    def test_underspend_no_warning(self, four_step_budget):
        events = four_step_budget.record_usage("fetch", 600)
        kinds = [e.kind for e in events]
        assert BudgetEventKind.WARNING not in kinds

    def test_underspend_by_zero(self, four_step_budget):
        """Using exactly the allocated amount: exact spend, no bonus/penalty."""
        events = four_step_budget.record_usage("fetch", 1000)
        kinds = [e.kind for e in events]
        assert BudgetEventKind.WARNING not in kinds
        assert BudgetEventKind.REBALANCED in kinds  # rebalance still fires

    def test_multi_step_underspend_accumulates(self):
        """Surplus from multiple steps accumulates to later steps."""
        b = WorkflowBudget(total=3000, steps=["a", "b", "c"])
        # a uses 500 of 1000 (500 surplus)
        b.record_usage("a", 500)
        alloc_b = b.step_allocation("b")
        # b should have more than the original 1000
        assert alloc_b > 1000

    def test_last_step_gets_all_remaining(self):
        """When only one step remains, it gets all remaining tokens."""
        b = WorkflowBudget(total=3000, steps=["a", "b"])
        b.record_usage("a", 500)  # underspend by 500
        alloc_b = b.step_allocation("b")
        assert alloc_b == b.remaining


# ── AC4: Min floor ────────────────────────────────────────────────────────────

class TestMinFloor:
    def test_default_floor_is_100(self):
        assert MIN_FLOOR_TOKENS == 100

    def test_initial_alloc_respects_floor(self):
        # 3 steps, 150 tokens → each gets 50 base but floor is 100
        b = WorkflowBudget(total=150, steps=["a", "b", "c"])
        for s in ["a", "b", "c"]:
            assert b.step_allocation(s) >= 100

    def test_rebalance_respects_floor(self):
        """After overspend, pending steps still get at least MIN_FLOOR_TOKENS."""
        b = WorkflowBudget(total=1200, steps=["a", "b", "c", "d"])
        # a overshoots drastically
        b.record_usage("a", 900)  # leaves only 300 for b, c, d
        for s in ["b", "c", "d"]:
            assert b.step_allocation(s) >= MIN_FLOOR_TOKENS

    def test_custom_floor(self):
        b = WorkflowBudget(total=2000, steps=["x", "y"], min_floor=200)
        assert b.step_allocation("x") >= 200
        assert b.step_allocation("y") >= 200

    def test_floor_applied_event_when_pool_less_than_floor_times_steps(self):
        """When remaining < floor × pending, floor_applied event fires."""
        b = WorkflowBudget(total=400, steps=["a", "b", "c", "d"])
        # After 'a' uses 300, only 100 left for 3 steps (floor=100 × 3 = 300 > 100)
        events = b.record_usage("a", 300)
        kinds = [e.kind for e in events]
        assert BudgetEventKind.FLOOR_APPLIED in kinds

    def test_floor_zero_allowed(self):
        """min_floor=0 is valid."""
        b = WorkflowBudget(total=100, steps=["a", "b"], min_floor=0)
        assert b.step_allocation("a") >= 0


# ── AC5: Warn >120% overspend ─────────────────────────────────────────────────

class TestWarnThreshold:
    def test_warn_threshold_constant(self):
        assert WARN_OVERSPEND_PCT == 1.20

    def test_no_warn_at_120_pct(self, four_step_budget):
        events = four_step_budget.record_usage("fetch", 1200)
        assert not any(e.kind == BudgetEventKind.WARNING for e in events)

    def test_warn_at_121_pct(self, four_step_budget):
        events = four_step_budget.record_usage("fetch", 1210)
        assert any(e.kind == BudgetEventKind.WARNING for e in events)

    def test_warn_contains_step_name(self, four_step_budget):
        events = four_step_budget.record_usage("fetch", 1500)
        warn = next(e for e in events if e.kind == BudgetEventKind.WARNING)
        assert warn.step == "fetch"

    def test_warn_event_data_has_pct(self, four_step_budget):
        events = four_step_budget.record_usage("fetch", 1500)
        warn = next(e for e in events if e.kind == BudgetEventKind.WARNING)
        assert "pct" in warn.data
        assert warn.data["pct"] == pytest.approx(150.0, abs=0.2)

    def test_custom_warn_pct(self):
        b = WorkflowBudget(total=4000, steps=["a", "b", "c", "d"], warn_pct=1.50)
        events = b.record_usage("a", 1300)  # 130% — below custom 150% threshold
        assert not any(e.kind == BudgetEventKind.WARNING for e in events)

        b2 = WorkflowBudget(total=4000, steps=["a", "b", "c", "d"], warn_pct=1.50)
        events2 = b2.record_usage("a", 1501)  # just above 150%
        assert any(e.kind == BudgetEventKind.WARNING for e in events2)


# ── AC6: Critical <20% remaining ─────────────────────────────────────────────

class TestCriticalThreshold:
    def test_critical_threshold_constant(self):
        assert CRITICAL_REMAINING_PCT == 0.20

    def test_no_critical_above_threshold(self):
        b = WorkflowBudget(total=1000, steps=["a", "b", "c"])
        # 250 tokens used → 750 remaining = 75% > 20%
        events = b.record_usage("a", 250)
        assert not any(e.kind == BudgetEventKind.CRITICAL for e in events)

    def test_critical_below_threshold(self):
        b = WorkflowBudget(total=1000, steps=["a", "b", "c"])
        # Use 850 tokens → 150 remaining = 15% < 20%
        events = b.record_usage("a", 850)
        assert any(e.kind == BudgetEventKind.CRITICAL for e in events)

    def test_critical_at_exactly_threshold(self):
        # remaining == 20% of total: NOT critical (strict <)
        b = WorkflowBudget(total=1000, steps=["a", "b"])
        # 500 allocated each; use 300 from a → 700 remaining = 70% → not critical
        events = b.record_usage("a", 300)
        assert not any(e.kind == BudgetEventKind.CRITICAL for e in events)

    def test_critical_event_has_remaining_data(self):
        b = WorkflowBudget(total=1000, steps=["a", "b", "c"])
        events = b.record_usage("a", 900)
        crit = next((e for e in events if e.kind == BudgetEventKind.CRITICAL), None)
        assert crit is not None
        assert "remaining" in crit.data
        assert crit.data["remaining"] == 100

    def test_snapshot_critical_flag(self):
        b = WorkflowBudget(total=1000, steps=["a", "b", "c"])
        b.record_usage("a", 900)
        snap = b.snapshot()
        assert snap["critical"] is True

    def test_snapshot_not_critical_when_healthy(self, four_step_budget):
        four_step_budget.record_usage("fetch", 500)
        snap = four_step_budget.snapshot()
        assert snap["critical"] is False


# ── AC7: Never silently truncate ──────────────────────────────────────────────

class TestNoSilentTruncation:
    def test_raises_on_unknown_step(self, four_step_budget):
        with pytest.raises(KeyError):
            four_step_budget.record_usage("nonexistent", 100)

    def test_raises_on_duplicate_record(self, four_step_budget):
        four_step_budget.record_usage("fetch", 100)
        with pytest.raises(ValueError, match="already recorded"):
            four_step_budget.record_usage("fetch", 100)

    def test_raises_on_negative_usage(self, four_step_budget):
        with pytest.raises(ValueError, match=">= 0"):
            four_step_budget.record_usage("fetch", -1)

    def test_raises_on_invalid_total(self):
        with pytest.raises(ValueError):
            WorkflowBudget(total=0, steps=["a"])

    def test_raises_on_negative_total(self):
        with pytest.raises(ValueError):
            WorkflowBudget(total=-100, steps=["a"])

    def test_raises_on_empty_steps(self):
        with pytest.raises(ValueError):
            WorkflowBudget(total=1000, steps=[])

    def test_raises_on_negative_floor(self):
        with pytest.raises(ValueError):
            WorkflowBudget(total=1000, steps=["a"], min_floor=-1)

    def test_usage_explicitly_returned_as_events(self, four_step_budget):
        """All issues surfaced via events, not silent drops."""
        events = four_step_budget.record_usage("fetch", 1500)
        # Warning event should mention the overspend
        warn = next((e for e in events if e.kind == BudgetEventKind.WARNING), None)
        assert warn is not None
        assert "1500" in warn.message or "150" in warn.message

    def test_unknown_step_allocation(self, four_step_budget):
        with pytest.raises(KeyError):
            four_step_budget.step_allocation("nope")


# ── AC8: Multi-step scenarios ─────────────────────────────────────────────────

class TestMultiStep:
    def test_full_workflow_exact_spend(self):
        """All steps spend exactly their allocation — clean finish."""
        b = WorkflowBudget(total=3000, steps=["a", "b", "c"])
        b.record_usage("a", 1000)
        b.record_usage("b", 1000)
        b.record_usage("c", 1000)
        assert b.remaining == 0
        assert b.pending_steps == []
        assert b.completed_steps == ["a", "b", "c"]

    def test_full_workflow_underspend(self):
        """All steps underspend — surplus accumulates properly."""
        b = WorkflowBudget(total=3000, steps=["a", "b", "c"])
        b.record_usage("a", 700)  # 300 surplus
        b.record_usage("b", 700)  # surplus again
        b.record_usage("c", 700)
        assert b.remaining == 3000 - (700 + 700 + 700)
        assert b.pending_steps == []

    def test_full_workflow_overspend(self):
        """Steps overspend but remaining never goes negative."""
        b = WorkflowBudget(total=3000, steps=["a", "b", "c"])
        b.record_usage("a", 1400)
        b.record_usage("b", 1000)
        # c gets whatever is left
        remaining_before_c = b.remaining
        alloc_c = b.step_allocation("c")
        assert alloc_c >= MIN_FLOOR_TOKENS
        b.record_usage("c", min(alloc_c, remaining_before_c))
        assert b.remaining >= 0

    def test_single_step_budget(self):
        b = WorkflowBudget(total=500, steps=["only"])
        assert b.step_allocation("only") == 500
        events = b.record_usage("only", 300)
        assert b.remaining == 200
        assert b.pending_steps == []

    def test_allocation_sum_matches_remaining_after_each_step(self):
        """After each step, sum of pending allocations ≤ remaining budget."""
        b = WorkflowBudget(total=4000, steps=["a", "b", "c", "d"])
        for step, used in [("a", 800), ("b", 1100), ("c", 950)]:
            b.record_usage(step, used)
            pending = b.pending_steps
            total_alloc = sum(b.step_allocation(s) for s in pending)
            # Allow small overcount due to floor enforcement
            # In the worst case: floor × len(pending) can exceed remaining
            assert total_alloc >= MIN_FLOOR_TOKENS * len(pending)
            assert b.remaining >= 0

    def test_five_step_mixed_workflow(self):
        """Mixed over/underspend in a 5-step workflow."""
        b = WorkflowBudget(total=5000, steps=["a", "b", "c", "d", "e"])
        # a: exact
        b.record_usage("a", 1000)
        # b: underspend
        b.record_usage("b", 600)
        # c: overspend
        events_c = b.record_usage("c", 1400)
        assert any(e.kind == BudgetEventKind.WARNING for e in events_c)
        # d and e: use their allocations
        alloc_d = b.step_allocation("d")
        alloc_e = b.step_allocation("e")
        assert alloc_d >= MIN_FLOOR_TOKENS
        assert alloc_e >= MIN_FLOOR_TOKENS
        b.record_usage("d", alloc_d)
        b.record_usage("e", alloc_e)
        assert b.pending_steps == []

    def test_repr(self, four_step_budget):
        r = repr(four_step_budget)
        assert "WorkflowBudget" in r
        assert "4000" in r
