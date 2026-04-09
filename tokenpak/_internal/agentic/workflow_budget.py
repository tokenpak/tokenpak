"""tokenpak.agent.agentic.workflow_budget — Dynamic token budget rebalancing for workflows.

Tracks token budget across workflow steps and redistributes surplus/deficit
dynamically so steps always have a fair allocation without silent truncation.

Key behaviours:
  - Overspend  → redistribute remaining budget proportionally across pending steps
  - Underspend → bonus tokens given to the next-priority pending step
  - Min floor  → every pending step keeps at least MIN_FLOOR_TOKENS (default 100)
  - Warn       → step uses > 120% of allocation  (BudgetWarning)
  - Critical   → remaining budget < 20% of total (BudgetCritical)
  - Never silently truncate: callers receive explicit warnings/exceptions

Usage:
    budget = WorkflowBudget(
        total=8000,
        steps=["fetch", "compress", "summarise", "write"],
    )
    alloc = budget.step_allocation("fetch")  # 2000 tokens
    ...do work, use some tokens...
    events = budget.record_usage("fetch", tokens_used=1500)
    # events may contain BudgetWarning / BudgetCritical objects
    alloc = budget.step_allocation("compress")  # updated after rebalance
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence

# ── Constants ─────────────────────────────────────────────────────────────────

MIN_FLOOR_TOKENS: int = 100  # minimum per-step allocation
WARN_OVERSPEND_PCT: float = 1.20  # warn if step uses > 120% of its allocation
CRITICAL_REMAINING_PCT: float = 0.20  # critical if remaining < 20% of total budget


# ── Event types ───────────────────────────────────────────────────────────────


class BudgetEventKind(str, Enum):
    USAGE_RECORDED = "usage_recorded"
    REBALANCED = "rebalanced"
    WARNING = "warning"
    CRITICAL = "critical"
    FLOOR_APPLIED = "floor_applied"
    EXHAUSTED = "exhausted"


@dataclass
class BudgetEvent:
    kind: BudgetEventKind
    step: Optional[str]
    message: str
    data: Dict = field(default_factory=dict)

    def is_warning(self) -> bool:
        return self.kind in (BudgetEventKind.WARNING, BudgetEventKind.CRITICAL)

    def __str__(self) -> str:
        prefix = {
            BudgetEventKind.WARNING: "⚠️  WARN",
            BudgetEventKind.CRITICAL: "🔴 CRIT",
            BudgetEventKind.EXHAUSTED: "💀 EXHAUSTED",
        }.get(self.kind, "ℹ️ ")
        return f"{prefix} [{self.step or '*'}] {self.message}"


# ── Core class ────────────────────────────────────────────────────────────────


class WorkflowBudget:
    """Dynamic token-budget manager for a sequence of workflow steps.

    Args:
        total:      Total token budget for the entire workflow.
        steps:      Ordered list of step names (execution order).
        min_floor:  Minimum tokens guaranteed per pending step (default 100).
        warn_pct:   Overspend fraction that triggers a warning (default 1.20 = 120%).
        critical_pct: Remaining-budget fraction that triggers a critical alert
                      (default 0.20 = 20% of total remaining is critical).
    """

    def __init__(
        self,
        total: int,
        steps: Sequence[str],
        min_floor: int = MIN_FLOOR_TOKENS,
        warn_pct: float = WARN_OVERSPEND_PCT,
        critical_pct: float = CRITICAL_REMAINING_PCT,
    ) -> None:
        if total <= 0:
            raise ValueError("total budget must be positive")
        if not steps:
            raise ValueError("steps must be non-empty")
        if min_floor < 0:
            raise ValueError("min_floor must be >= 0")

        self._total = total
        self._steps: List[str] = list(steps)
        self._min_floor = min_floor
        self._warn_pct = warn_pct
        self._critical_pct = critical_pct

        # Set of steps not yet completed
        self._pending: List[str] = list(steps)
        # Per-step allocations (tokens reserved)
        self._allocations: Dict[str, int] = {}
        # Per-step actual usage
        self._usage: Dict[str, int] = {}
        # Remaining unspent budget
        self._remaining: int = total

        # Initial even split
        self._allocations = self._even_split(self._steps, self._remaining)

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def total(self) -> int:
        return self._total

    @property
    def remaining(self) -> int:
        return self._remaining

    @property
    def pending_steps(self) -> List[str]:
        return list(self._pending)

    @property
    def completed_steps(self) -> List[str]:
        return [s for s in self._steps if s not in self._pending]

    def step_allocation(self, step: str) -> int:
        """Return current token allocation for *step*."""
        if step not in self._allocations:
            raise KeyError(f"Unknown step '{step}'")
        return self._allocations[step]

    def step_usage(self, step: str) -> Optional[int]:
        """Return recorded usage for *step*, or None if not yet recorded."""
        return self._usage.get(step)

    def record_usage(self, step: str, tokens_used: int) -> List[BudgetEvent]:
        """Record actual token usage for a completed step and rebalance.

        Args:
            step:        Step name (must be in pending steps).
            tokens_used: Actual tokens consumed by this step.

        Returns:
            List of BudgetEvent objects (warnings, criticals, rebalance info).

        Raises:
            KeyError:   If step is unknown.
            ValueError: If step was already recorded.
            ValueError: If tokens_used is negative.
        """
        if step not in self._steps:
            raise KeyError(f"Unknown step '{step}'")
        if step not in self._pending:
            raise ValueError(f"Step '{step}' already recorded")
        if tokens_used < 0:
            raise ValueError("tokens_used must be >= 0")

        events: List[BudgetEvent] = []

        allocation = self._allocations[step]
        self._usage[step] = tokens_used
        self._pending.remove(step)

        # Event: usage recorded
        events.append(
            BudgetEvent(
                kind=BudgetEventKind.USAGE_RECORDED,
                step=step,
                message=f"used {tokens_used}/{allocation} tokens",
                data={"used": tokens_used, "allocated": allocation},
            )
        )

        # Check overspend warning (>120% of allocation)
        if allocation > 0 and tokens_used > allocation * self._warn_pct:
            overpct = round(tokens_used / allocation * 100, 1)
            events.append(
                BudgetEvent(
                    kind=BudgetEventKind.WARNING,
                    step=step,
                    message=f"overspend {overpct}% of allocation ({tokens_used} > {allocation})",
                    data={"pct": overpct, "used": tokens_used, "allocated": allocation},
                )
            )

        # Deduct from remaining
        self._remaining -= tokens_used
        if self._remaining < 0:
            self._remaining = 0

        # Check critical remaining threshold
        critical_threshold = math.floor(self._total * self._critical_pct)
        if self._remaining < critical_threshold:
            pct_left = round(self._remaining / self._total * 100, 1) if self._total else 0
            events.append(
                BudgetEvent(
                    kind=BudgetEventKind.CRITICAL,
                    step=step,
                    message=f"only {self._remaining} tokens remain ({pct_left}% of total {self._total})",
                    data={"remaining": self._remaining, "total": self._total, "pct_left": pct_left},
                )
            )

        if self._remaining == 0 and self._pending:
            events.append(
                BudgetEvent(
                    kind=BudgetEventKind.EXHAUSTED,
                    step=step,
                    message=f"budget exhausted; {len(self._pending)} step(s) still pending",
                    data={"pending": list(self._pending)},
                )
            )

        # Rebalance remaining budget across pending steps
        if self._pending:
            events.extend(self._rebalance(triggering_step=step, delta=allocation - tokens_used))

        return events

    def snapshot(self) -> Dict:
        """Return a summary dict of current budget state."""
        return {
            "total": self._total,
            "remaining": self._remaining,
            "spent": self._total - self._remaining,
            "pct_remaining": round(self._remaining / self._total * 100, 1) if self._total else 0,
            "pending_steps": list(self._pending),
            "completed_steps": self.completed_steps,
            "allocations": dict(self._allocations),
            "usage": dict(self._usage),
            "critical": self._remaining < math.floor(self._total * self._critical_pct),
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _even_split(self, steps: List[str], pool: int) -> Dict[str, int]:
        """Split *pool* tokens evenly across *steps*, respecting min_floor."""
        if not steps:
            return {}
        n = len(steps)
        base = pool // n
        remainder = pool - base * n

        # Apply floor
        floored = max(base, self._min_floor)
        allocs: Dict[str, int] = {}
        for i, s in enumerate(steps):
            allocs[s] = floored + (1 if i < remainder else 0)

        return allocs

    def _rebalance(self, triggering_step: str, delta: int) -> List[BudgetEvent]:
        """Redistribute remaining budget across pending steps.

        delta > 0 → underspend (surplus available)
        delta < 0 → overspend (less than expected remains)
        delta = 0 → exact spend (even split of actual remaining)
        """
        events: List[BudgetEvent] = []

        if not self._pending:
            return events

        # Always do a fresh even split of actual remaining
        new_allocs = self._even_split(self._pending, self._remaining)

        # If there was a surplus (underspend), give bonus to first pending step
        if delta > 0 and self._pending:
            first = self._pending[0]
            bonus = delta  # all surplus to next priority step
            new_allocs[first] = new_allocs.get(first, 0) + bonus

            events.append(
                BudgetEvent(
                    kind=BudgetEventKind.REBALANCED,
                    step=triggering_step,
                    message=(f"underspend by {delta} tokens — bonus +{bonus} → '{first}'"),
                    data={"delta": delta, "bonus_step": first, "bonus_tokens": bonus},
                )
            )
        elif delta < 0:
            events.append(
                BudgetEvent(
                    kind=BudgetEventKind.REBALANCED,
                    step=triggering_step,
                    message=(
                        f"overspend by {abs(delta)} tokens — redistribution applied "
                        f"across {len(self._pending)} pending step(s)"
                    ),
                    data={"delta": delta, "pending": list(self._pending)},
                )
            )
        else:
            events.append(
                BudgetEvent(
                    kind=BudgetEventKind.REBALANCED,
                    step=triggering_step,
                    message=f"exact spend — even split across {len(self._pending)} pending step(s)",
                    data={"delta": 0, "pending": list(self._pending)},
                )
            )

        # Enforce floor on every pending step; cap total to remaining
        total_after_floor = sum(max(v, self._min_floor) for v in new_allocs.values())
        if total_after_floor > self._remaining and self._remaining >= 0:
            # Re-split: floor forces may exceed remaining; clamp proportionally
            for s in self._pending:
                clamped = max(self._min_floor, new_allocs.get(s, 0))
                new_allocs[s] = clamped

            # If still over, apply floor exactly and split the remainder
            floor_total = self._min_floor * len(self._pending)
            if floor_total <= self._remaining:
                extra = self._remaining - floor_total
                # Distribute extra one token at a time to preserve order fairness
                for i, s in enumerate(self._pending):
                    give = extra // len(self._pending) + (
                        1 if i < extra % len(self._pending) else 0
                    )
                    new_allocs[s] = self._min_floor + give
            else:
                # Even floor is too much — give min_floor to each anyway
                for s in self._pending:
                    new_allocs[s] = self._min_floor
                events.append(
                    BudgetEvent(
                        kind=BudgetEventKind.FLOOR_APPLIED,
                        step=triggering_step,
                        message=(
                            f"remaining ({self._remaining}) < floor×steps "
                            f"({self._min_floor}×{len(self._pending)}={floor_total}); "
                            f"floor applied to all pending steps"
                        ),
                        data={
                            "remaining": self._remaining,
                            "floor": self._min_floor,
                            "pending": list(self._pending),
                        },
                    )
                )

        # Commit
        for s in self._pending:
            self._allocations[s] = new_allocs.get(s, self._min_floor)

        return events

    def __repr__(self) -> str:
        return (
            f"<WorkflowBudget total={self._total} remaining={self._remaining} "
            f"pending={self._pending}>"
        )
