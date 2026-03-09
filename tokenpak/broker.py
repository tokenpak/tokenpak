# SPDX-License-Identifier: MIT
"""Autonomous Broker — Active Routing for TokenPak Phase 3.2.

Once statistical confidence is met (≥MIN_SAMPLES per task_type), the broker
autonomously downgrades or upgrades model selection based on historical
acceptance rates and task complexity.

Decision flow:
  1. Check force_model — if set, pass-through immediately.
  2. Check confidence gate — below threshold, pass-through.
  3. Downgrade: cheap model has >95% acceptance → route there.
  4. Upgrade: complex task + low acceptance on current model → upgrade.
  5. Log decision + badge response.
"""

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .elo import DEFAULT_ELO_PATH, EloRatings
from .routing_ledger import DEFAULT_LEDGER_PATH, RoutingLedger

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Minimum accepted/rejected samples before the broker acts on a (model, task_type)
MIN_SAMPLES = int(os.environ.get("TOKENPAK_BROKER_MIN_SAMPLES", "50"))

# Thresholds
DOWNGRADE_ACCEPTANCE_THRESHOLD = 0.95  # cheap model must beat this to get traffic
UPGRADE_COMPLEXITY_THRESHOLD = 7.0  # score above which we consider upgrading
UPGRADE_ACCEPTANCE_FLOOR = 0.50  # below this → upgrade

# Cooldown: after a rejected downgrade, skip N transactions before trying again
DOWNGRADE_COOLDOWN = 10

# Default path for model tiers
DEFAULT_TIERS_PATH = str(Path(__file__).parent.parent / "model_tiers.json")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class RoutingDecision:
    original_model: str
    selected_model: str
    action: str  # passthrough | downgrade | upgrade
    confidence: float  # sample-based confidence score (0.0–1.0)
    reason: str
    badge: str = ""  # Badge text to inject into response


# ---------------------------------------------------------------------------
# Model tier helpers
# ---------------------------------------------------------------------------


def _load_tiers(tiers_path: str = DEFAULT_TIERS_PATH) -> Dict[str, int]:
    """Load model cost tiers from JSON. Returns empty dict on failure."""
    try:
        return json.loads(Path(tiers_path).read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def get_tier(model: str, tiers: Dict[str, int]) -> int:
    """Return tier for a model; strip provider prefix if needed. Default: 2."""
    if model in tiers:
        return tiers[model]
    # Try stripping provider prefix (e.g. "anthropic/claude-sonnet-4-6")
    short = model.split("/")[-1]
    return tiers.get(short, 2)


def cheaper_models(model: str, tiers: Dict[str, int]) -> List[str]:
    """Return all known models with a lower tier than the given model, sorted cheapest first."""
    current_tier = get_tier(model, tiers)
    candidates = [(m, t) for m, t in tiers.items() if not m.startswith("_") and t < current_tier]
    return [m for m, _ in sorted(candidates, key=lambda x: x[1])]


def more_capable_models(model: str, tiers: Dict[str, int]) -> List[str]:
    """Return all known models with a higher tier, sorted most capable first."""
    current_tier = get_tier(model, tiers)
    candidates = [(m, t) for m, t in tiers.items() if not m.startswith("_") and t > current_tier]
    return [m for m, _ in sorted(candidates, key=lambda x: x[1], reverse=True)]


# ---------------------------------------------------------------------------
# Broker
# ---------------------------------------------------------------------------


class Broker:
    """
    Autonomous routing broker. Thread-safe.

    Uses RoutingLedger for historical acceptance rates and EloRatings for
    per-model performance tracking.
    """

    def __init__(
        self,
        ledger_path: str = DEFAULT_LEDGER_PATH,
        elo_path: str = DEFAULT_ELO_PATH,
        tiers_path: str = DEFAULT_TIERS_PATH,
        min_samples: int = MIN_SAMPLES,
    ):
        self._ledger = RoutingLedger(ledger_path)
        self._elo = EloRatings(elo_path)
        self._tiers = _load_tiers(tiers_path)
        self._min_samples = min_samples
        self._lock = threading.Lock()

        # Cooldown tracker: model → remaining cooldown count
        # Cooldown is decremented on each passthrough after a rejected downgrade.
        self._cooldown: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(
        self,
        model: str,
        task_type: str,
        complexity_score: float,
        force_model: bool = False,
    ) -> RoutingDecision:
        """
        Decide whether to pass-through, downgrade, or upgrade a request.

        Args:
            model:            The model the caller originally requested.
            task_type:        Task type string (from complexity.py TaskType).
            complexity_score: Float 0.0–10.0 from score_complexity().
            force_model:      If True, skip all routing logic and pass-through.

        Returns:
            RoutingDecision with selected_model, action, confidence, reason, badge.
        """
        # 1. Force override — user explicitly requested this model
        if force_model:
            return RoutingDecision(
                original_model=model,
                selected_model=model,
                action="passthrough",
                confidence=1.0,
                reason="force_model override",
            )

        # 2. Confidence gate
        confidence = self._confidence(model, task_type)
        if confidence < 1.0:
            return RoutingDecision(
                original_model=model,
                selected_model=model,
                action="passthrough",
                confidence=confidence,
                reason=f"below confidence threshold ({self._ledger.sample_count(model, task_type)}/{self._min_samples} samples)",
            )

        # 3. Check downgrade opportunity
        downgrade = self._try_downgrade(model, task_type)
        if downgrade is not None:
            return downgrade

        # 4. Check upgrade need
        upgrade = self._try_upgrade(model, task_type, complexity_score, confidence)
        if upgrade is not None:
            return upgrade

        # 5. Pass-through
        return RoutingDecision(
            original_model=model,
            selected_model=model,
            action="passthrough",
            confidence=confidence,
            reason="within acceptable performance range",
        )

    def record_outcome(
        self,
        transaction_id: int,
        accepted: bool,
        reason: Optional[str] = None,
    ) -> bool:
        """
        Record outcome and update Elo. Trigger cooldown on rejected downgrade.
        """
        txn = self._ledger.get_transaction(transaction_id)
        ok = self._ledger.record_outcome(transaction_id, accepted, reason)
        if ok and txn:
            self._elo.update_elo(txn["model_used"], txn["task_type"], accepted)
            # If this was a downgraded transaction and it was rejected → start cooldown
            if not accepted and txn.get("routing_action") == "downgrade":
                with self._lock:
                    self._cooldown[txn["model_used"]] = DOWNGRADE_COOLDOWN
        return ok

    def is_confident(self, model: str, task_type: str) -> bool:
        """Return True when sample count meets the minimum threshold."""
        return self._ledger.sample_count(model, task_type) >= self._min_samples

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _confidence(self, model: str, task_type: str) -> float:
        """
        Normalised confidence in [0.0, 1.0].
        Returns 1.0 when sample count ≥ min_samples, else proportion.
        """
        count = self._ledger.sample_count(model, task_type)
        return min(1.0, count / max(self._min_samples, 1))

    def _in_cooldown(self, model: str) -> bool:
        return self._cooldown.get(model, 0) > 0

    def _tick_cooldown(self, model: str):
        """Decrement cooldown counter if active."""
        if self._cooldown.get(model, 0) > 0:
            self._cooldown[model] -= 1

    def _try_downgrade(self, model: str, task_type: str) -> Optional[RoutingDecision]:
        """
        Attempt to downgrade to a cheaper model.
        Returns RoutingDecision if downgrade is warranted, else None.
        """
        candidates = cheaper_models(model, self._tiers)
        for cheap_model in candidates:
            # Skip candidates in cooldown (they were recently rejected)
            with self._lock:
                if self._in_cooldown(cheap_model):
                    self._tick_cooldown(cheap_model)
                    continue

            count = self._ledger.sample_count(cheap_model, task_type)
            if count < self._min_samples:
                continue  # Not enough data on this cheaper model
            rate = self._ledger.acceptance_rate(cheap_model, task_type)
            if rate > DOWNGRADE_ACCEPTANCE_THRESHOLD:
                badge = f"[🟢 TokenPak: routed to {cheap_model} (cheaper, {rate:.0%} acceptance)]"
                return RoutingDecision(
                    original_model=model,
                    selected_model=cheap_model,
                    action="downgrade",
                    confidence=min(1.0, count / self._min_samples),
                    reason=f"{cheap_model} has {rate:.1%} acceptance rate on {task_type}",
                    badge=badge,
                )
        return None

    def _try_upgrade(
        self,
        model: str,
        task_type: str,
        complexity_score: float,
        confidence: float,
    ) -> Optional[RoutingDecision]:
        """
        Attempt to upgrade to a more capable model.
        Returns RoutingDecision if upgrade is warranted, else None.
        """
        if complexity_score <= UPGRADE_COMPLEXITY_THRESHOLD:
            return None

        current_rate = self._ledger.acceptance_rate(model, task_type)
        if current_rate >= UPGRADE_ACCEPTANCE_FLOOR:
            return None  # Current model performing adequately

        candidates = more_capable_models(model, self._tiers)
        if not candidates:
            return None

        target = candidates[0]  # Most capable first
        badge = (
            f"[🔵 TokenPak: upgraded to {target} "
            f"(complexity {complexity_score:.1f}/10, {current_rate:.0%} acceptance on {model})]"
        )
        return RoutingDecision(
            original_model=model,
            selected_model=target,
            action="upgrade",
            confidence=confidence,
            reason=(
                f"complexity {complexity_score:.1f} > {UPGRADE_COMPLEXITY_THRESHOLD} "
                f"and {model} acceptance {current_rate:.1%} < {UPGRADE_ACCEPTANCE_FLOOR:.0%} on {task_type}"
            ),
            badge=badge,
        )
