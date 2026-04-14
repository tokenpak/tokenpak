"""tokenpak/_internal/budget_controller.py

USD monthly spend enforcement — stateless check() for the proxy request gate.

Wired into proxy_v4.py as part of TRIX-02 / pmgtm initiative (AC-1.2).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class BudgetCheckResult:
    """Result of a budget check against the monthly spend limit."""

    exceeded: bool
    limit_usd: Optional[float]
    spent_usd: float
    reset_at: str  # ISO 8601 UTC, e.g. "2026-05-01T00:00:00Z"


class BudgetController:
    """Stateless gate — check whether monthly USD spend limit is exceeded.

    Takes a pre-computed spend figure (from the module-level cache in
    proxy_v4.py) so there is no DB access here.  One controller instance
    can be shared across threads.
    """

    def check(
        self,
        limit_usd: Optional[float],
        spent_usd: float,
    ) -> BudgetCheckResult:
        """Return BudgetCheckResult for the given limit / spend pair.

        Parameters
        ----------
        limit_usd:
            Monthly budget limit in USD.  None means unlimited — the result
            will always have ``exceeded=False``.
        spent_usd:
            Current month's spend in USD (from the proxy module-level cache).

        Notes
        -----
        ``reset_at`` is always the first second of the *next* calendar month in
        UTC, formatted as ``YYYY-MM-DDTHH:MM:SSZ``.
        """
        exceeded = limit_usd is not None and spent_usd >= limit_usd
        return BudgetCheckResult(
            exceeded=exceeded,
            limit_usd=limit_usd,
            spent_usd=spent_usd,
            reset_at=_next_month_start_utc(),
        )


def _next_month_start_utc() -> str:
    """Return the first moment of the next calendar month as ISO 8601 UTC string."""
    today = date.today()
    if today.month == 12:
        next_month = date(today.year + 1, 1, 1)
    else:
        next_month = date(today.year, today.month + 1, 1)
    dt = datetime(
        next_month.year,
        next_month.month,
        next_month.day,
        0, 0, 0,
        tzinfo=timezone.utc,
    )
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
