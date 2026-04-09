"""
tokenpak.license.gates — @requires_tier decorator and TierRequiredError.

Usage:
    from tokenpak.license.gates import requires_tier, TierRequiredError
    from tokenpak.license.tier import LicenseTier

    @requires_tier(LicenseTier.PRO, message="Replay is a Pro feature — start a free trial: https://portal.tokenpak.io/trial")
    def cmd_replay_list(args):
        ...

On invocation:
  - Active tier >= required_tier  → the wrapped function runs normally.
  - Active tier < required_tier   → TierRequiredError is raised with the CTA message.

The caller (proxy HTTP handler, CLI main, FastAPI exception handler) is
responsible for converting TierRequiredError into an appropriate response
(HTTP 402 or printed CTA + non-zero exit).
"""
from __future__ import annotations

from functools import wraps
from typing import Callable, Optional, TypeVar, overload

from tokenpak.license.tier import LicenseTier

F = TypeVar("F", bound=Callable)

# Default trial CTA — overridden per feature via the `message` argument.
_DEFAULT_CTA = (
    "This is a Pro feature — start a free trial: https://portal.tokenpak.io/trial"
)


class TierRequiredError(Exception):
    """Raised when a feature requires a higher license tier than currently active."""

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        feature: str = "",
        required: LicenseTier = LicenseTier.PRO,
        current: Optional[LicenseTier] = None,
    ) -> None:
        self.feature = feature
        self.required = required
        self.current = current
        self.cta = message or _DEFAULT_CTA
        super().__init__(self.cta)


def requires_tier(
    tier: LicenseTier,
    message: Optional[str] = None,
) -> Callable[[F], F]:
    """
    Decorator factory: gate a function behind a minimum license tier.

    Args:
        tier:    The minimum LicenseTier required to call this function.
        message: Human-readable CTA string.  Defaults to the generic trial CTA.

    The decorator is zero-cost at import time; tier is checked on every call.
    """

    def decorator(func: F) -> F:
        feature_name = getattr(func, "__name__", repr(func))
        cta = message or (
            f"{feature_name} requires {tier.name} tier — "
            f"start a free trial: https://portal.tokenpak.io/trial"
        )

        @wraps(func)
        def wrapper(*args, **kwargs):
            from tokenpak.license.loader import get_active_tier

            active = get_active_tier()
            if active < tier:
                raise TierRequiredError(
                    message=cta,
                    feature=feature_name,
                    required=tier,
                    current=active,
                )
            return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
