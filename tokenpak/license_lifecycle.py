"""
tokenpak.license_lifecycle
==========================
License lifecycle manager: tracks VALID -> EXPIRING -> GRACE -> EXPIRED states
and resolves active features based on the current plan tier.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional, Set

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GRACE_PERIOD_DAYS: int = 7
"""Days after expiry during which a license stays in GRACE state."""

EXPIRING_WARNING_DAYS: int = 14
"""Days before expiry at which the license transitions to EXPIRING state."""


# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------


class LicenseLifecycleState(str, Enum):
    VALID = "valid"
    EXPIRING = "expiring"
    GRACE = "grace"
    EXPIRED = "expired"


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class LicenseLifecycleManager:
    """Manages license lifecycle state transitions and active feature sets."""

    def __init__(
        self,
        get_plan_fn: Callable[[], Any],
        feature_resolver: Callable[[Any], Set[str]],
        revalidation_interval: int = 3600,
    ) -> None:
        self._get_plan = get_plan_fn
        self._feature_resolver = feature_resolver
        self._interval = revalidation_interval

        self._lock = threading.Lock()
        self._state: LicenseLifecycleState = LicenseLifecycleState.VALID
        self._active_features: Set[str] = set()
        self._warning_message: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    @property
    def state(self) -> LicenseLifecycleState:
        return self._state

    @property
    def active_features(self) -> Set[str]:
        return self._active_features

    @property
    def warning_message(self) -> Optional[str]:
        return self._warning_message

    def start(self) -> None:
        self._evaluate()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="license-lifecycle")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop_event.wait(timeout=self._interval):
            self._evaluate()

    def _evaluate(self) -> None:
        try:
            plan = self._get_plan()
        except Exception:
            return

        tier = plan.tier
        expires_at: Optional[str] = plan.expires_at

        now = datetime.now(tz=timezone.utc)
        new_state = LicenseLifecycleState.VALID
        warning: Optional[str] = None

        if expires_at is not None:
            try:
                expiry = datetime.fromisoformat(expires_at)
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                expiry = None

            if expiry is not None:
                delta = expiry - now
                days_until = delta.total_seconds() / 86400

                if days_until < 0:
                    days_overdue = abs(days_until)
                    if days_overdue <= GRACE_PERIOD_DAYS:
                        new_state = LicenseLifecycleState.GRACE
                        warning = (
                            f"License expired. Grace period ends in "
                            f"{GRACE_PERIOD_DAYS - int(days_overdue)} day(s). "
                            f"Renew at tokenpak.dev"
                        )
                    else:
                        new_state = LicenseLifecycleState.EXPIRED
                elif days_until <= EXPIRING_WARNING_DAYS:
                    new_state = LicenseLifecycleState.EXPIRING
                    warning = (
                        f"License expiring in {int(days_until)} day(s). " f"Renew at tokenpak.dev"
                    )

        if new_state == LicenseLifecycleState.EXPIRED:
            features: Set[str] = set()
        else:
            try:
                features = self._feature_resolver(tier)
            except Exception:
                features = set()

        with self._lock:
            self._state = new_state
            self._active_features = features
            self._warning_message = warning


__all__ = [
    "GRACE_PERIOD_DAYS",
    "EXPIRING_WARNING_DAYS",
    "LicenseLifecycleState",
    "LicenseLifecycleManager",
]
