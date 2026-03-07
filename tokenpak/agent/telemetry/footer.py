"""TokenPak Agent Telemetry Footer — render compression stats after each response."""

from __future__ import annotations

import logging
from typing import List, Optional

from .collector import RequestStats, SessionStats

logger = logging.getLogger(__name__)


def log_failover_event(chain: List[str], original: str, final: str, reason: str = "") -> None:
    """Log a failover event at INFO level.

    Args:
        chain: List of providers tried (in order)
        original: Original provider that was requested
        final: Final provider that succeeded
        reason: Reason for failover (e.g., "rate_limit", "timeout")
    """
    chain_str = "→".join(chain)
    reason_part = f" ({reason})" if reason else ""
    logger.info(f"Failover: {chain_str}{reason_part}")


def render_footer_oneline(stats: RequestStats) -> str:
    """Single-line footer for inline use."""
    return stats.footer_oneline


def render_footer(stats: RequestStats, session: Optional[SessionStats] = None) -> str:
    """Multi-line footer block with optional session totals.

    Example output::

        ─────────────────────────────────────────
        ⚡ TokenPak  -312 tokens (18%) | $0.003 saved
        📊 Session   47 reqs | -14,911 tokens | $1.24 saved
        ─────────────────────────────────────────
    """
    sep = "─" * 41

    if stats.tokens_saved == 0:
        req_line = "⚡ TokenPak  0 tokens saved"
    else:
        req_line = (
            f"⚡ TokenPak  -{stats.tokens_saved:,} tokens "
            f"({stats.percent_saved:.0f}%) | ${stats.cost_saved:.3f} saved"
        )

    lines = [sep, req_line]

    if session and session.session_requests > 0:
        sess_line = (
            f"📊 Session   {session.session_requests} reqs | "
            f"-{session.session_total_saved:,} tokens | "
            f"${session.session_total_cost_saved:.2f} saved"
        )
        lines.append(sess_line)

    lines.append(sep)
    return "\n".join(lines)


def render_footer_compact(stats: RequestStats) -> str:
    """Ultra-compact single-token footer for low-noise environments."""
    if stats.tokens_saved == 0:
        return "⚡0"
    return f"⚡-{stats.tokens_saved:,}t"


def render_footer_with_failover(
    stats: RequestStats,
    failover_indicator: Optional[str] = None,
    session: Optional[SessionStats] = None,
) -> str:
    """Multi-line footer block with optional failover indicator.

    Example output when failover occurred::

        ─────────────────────────────────────────
        ⚡ TokenPak  -312 tokens (18%) | $0.003 saved
        ⚠️ failover:openai (anthropic 429 rate_limit)
        ─────────────────────────────────────────

    Args:
        stats: Request compression stats
        failover_indicator: Failover indicator string from FailoverEventLog
        session: Optional session totals

    Returns:
        Formatted footer string (multi-line)
    """
    base = render_footer(stats, session=session)
    if not failover_indicator:
        return base

    lines = base.split("\n")
    lines[-1]  # last line is the separator
    # Insert failover line before final separator
    lines.insert(-1, failover_indicator)
    return "\n".join(lines)
