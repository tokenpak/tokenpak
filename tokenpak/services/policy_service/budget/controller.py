# SPDX-License-Identifier: Apache-2.0
"""Budget controller for dynamic context token tiers.

Maps intent + complexity to a target token budget tier and determines whether
an escalation is allowed when retrieval coverage is too low.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class IntentClass(str, Enum):
    """Intent classes produced by deterministic intent classifier."""

    GEN_Q = "GEN_Q"
    CODE_Q = "CODE_Q"
    CODE_EDIT = "CODE_EDIT"
    DEBUG = "DEBUG"
    DOC_EDIT = "DOC_EDIT"
    PLAN = "PLAN"
    REVIEW = "REVIEW"


DEFAULT_TIER_TOKENS: dict[str, int] = {
    "T0_8K": 8_000,
    "T1_16K": 16_000,
    "T2_32K": 32_000,
    "T3_64K": 64_000,
    "T4_128K": 128_000,
}

DEFAULT_TIER_ORDER: list[str] = ["T0_8K", "T1_16K", "T2_32K", "T3_64K", "T4_128K"]

TIER_MAP: dict[IntentClass, str] = {
    IntentClass.GEN_Q: "T0_8K",
    IntentClass.CODE_Q: "T1_16K",
    IntentClass.DEBUG: "T2_32K",
    IntentClass.CODE_EDIT: "T2_32K",
    IntentClass.DOC_EDIT: "T1_16K",
    IntentClass.PLAN: "T1_16K",
    IntentClass.REVIEW: "T2_32K",
}


@dataclass(frozen=True)
class ClassificationResult:
    """Lightweight classifier output contract used by budget controller."""

    intent: IntentClass
    complexity_score: float


@dataclass(frozen=True)
class BudgetDecision:
    target_tier: str
    target_token_budget: int
    reason: list[str]
    allow_escalation: bool
    max_auto_tier: str


class BudgetController:
    """Choose budget tier and escalation policy for a single turn."""

    def __init__(
        self,
        *,
        tier_map: Mapping[IntentClass, str] | None = None,
        tier_tokens: Mapping[str, int] | None = None,
        tier_order: list[str] | None = None,
        coverage_threshold: float = 0.55,
        max_auto_tier: str = "T3_64K",
        t4_intents: tuple[IntentClass, ...] = (IntentClass.CODE_EDIT, IntentClass.REVIEW),
    ) -> None:
        self.tier_map = dict(tier_map or TIER_MAP)
        self.tier_tokens = dict(tier_tokens or DEFAULT_TIER_TOKENS)
        self.tier_order = list(tier_order or DEFAULT_TIER_ORDER)
        self.coverage_threshold = coverage_threshold
        self.max_auto_tier = max_auto_tier
        self.t4_intents = set(t4_intents)

        if self.max_auto_tier not in self.tier_order:
            raise ValueError(f"max_auto_tier {self.max_auto_tier!r} not in tier_order")

    def decide(self, classification: ClassificationResult) -> BudgetDecision:
        tier = self.tier_map.get(classification.intent, "T1_16K")
        return BudgetDecision(
            target_tier=tier,
            target_token_budget=self.tier_tokens[tier],
            reason=[
                f"intent={classification.intent.value}",
                f"complexity={classification.complexity_score:.2f}",
            ],
            allow_escalation=True,
            max_auto_tier=self.max_auto_tier,
        )

    def maybe_escalate(
        self,
        decision: BudgetDecision,
        *,
        coverage_score: float,
        intent: IntentClass,
        multi_module_edit: bool = False,
    ) -> BudgetDecision:
        reasons = list(decision.reason)
        reasons.append(f"coverage={coverage_score:.2f}")

        if coverage_score >= self.coverage_threshold:
            reasons.append(f"no_escalation(coverage>={self.coverage_threshold:.2f})")
            return BudgetDecision(
                target_tier=decision.target_tier,
                target_token_budget=decision.target_token_budget,
                reason=reasons,
                allow_escalation=decision.allow_escalation,
                max_auto_tier=decision.max_auto_tier,
            )

        current_idx = self.tier_order.index(decision.target_tier)
        max_auto_idx = self.tier_order.index(self.max_auto_tier)

        # Automatic escalation: at most +1 tier, capped at T3 by default.
        next_idx = min(current_idx + 1, max_auto_idx)

        # Explicit T4 gate: only when CODE_EDIT/REVIEW, multi-module, and not fit in T3.
        if (
            intent in self.t4_intents
            and multi_module_edit
            and decision.target_tier == self.max_auto_tier
            and self.max_auto_tier != "T4_128K"
        ):
            next_idx = self.tier_order.index("T4_128K")
            reasons.append("escalated_to_T4(multi_module_edit)")
        elif next_idx > current_idx:
            reasons.append("escalated(+1_low_coverage)")
        else:
            reasons.append("escalation_capped")

        next_tier = self.tier_order[next_idx]
        return BudgetDecision(
            target_tier=next_tier,
            target_token_budget=self.tier_tokens[next_tier],
            reason=reasons,
            allow_escalation=decision.allow_escalation,
            max_auto_tier=decision.max_auto_tier,
        )
