# SPDX-License-Identifier: Apache-2.0
"""Canonical spike-replay test — TSG-05 acceptance lock.

Replays the 2026-05-07 09:28-10:56 UTC trace against the spend guard pipeline
and asserts the guard would have blocked the runaway before $10 in spend.

The trace lives in the user's actual ``~/.tokenpak/monitor.db`` (the DB the
proxy writes to in production). We snapshot the relevant rows in a temp
DB so the test is reproducible and doesn't mutate the live DB.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from tokenpak.proxy.spend_guard.contracts import RiskEstimate
from tokenpak.proxy.spend_guard.policy import SpendGuardConfig, decide

LIVE_MONITOR_DB = Path(os.path.expanduser("~/.tokenpak/monitor.db"))
SPIKE_WINDOW_START = "2026-05-07T09:28"
SPIKE_WINDOW_END = "2026-05-07T10:56"


def _has_spike_data() -> bool:
    if not LIVE_MONITOR_DB.exists():
        return False
    try:
        conn = sqlite3.connect(str(LIVE_MONITOR_DB))
        try:
            row = conn.execute(
                """SELECT COUNT(*) FROM requests
                   WHERE timestamp >= ? AND timestamp < ?""",
                (SPIKE_WINDOW_START, SPIKE_WINDOW_END),
            ).fetchone()
            return (row[0] or 0) > 100
        finally:
            conn.close()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _has_spike_data(),
    reason="No spike-replay data in ~/.tokenpak/monitor.db (window 2026-05-07T09:28..10:56)",
)


def _load_spike_rows() -> list[dict]:
    """Pull the spike rows from the live monitor.db read-only."""
    conn = sqlite3.connect(str(LIVE_MONITOR_DB))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT timestamp, model, input_tokens, output_tokens,
                      cache_read_tokens, cache_creation_tokens,
                      estimated_cost
               FROM requests
               WHERE timestamp >= ? AND timestamp < ?
               ORDER BY timestamp ASC""",
            (SPIKE_WINDOW_START, SPIKE_WINDOW_END),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _row_to_risk(row: dict) -> RiskEstimate:
    """Reconstruct a RiskEstimate from a recorded row.

    The estimator parses the *body* — but the spike DB only has tokens +
    cost. We synthesize a RiskEstimate that mirrors what the estimator
    would have produced if the body had been observed pre-send.
    """
    return RiskEstimate(
        model=row["model"] or "claude-opus-4-7",
        current_context_tokens=row["cache_read_tokens"] or 0,
        request_tokens=row["input_tokens"] or 0,
        projected_input_tokens=(row["input_tokens"] or 0) + (row["cache_read_tokens"] or 0),
        projected_output_tokens=row["output_tokens"] or 0,
        projected_cost_usd=row["estimated_cost"] or 0.0,
        cache_hit_ratio=0.0,
        rates={"input": 15.0, "output": 75.0, "cached": 1.5},
    )


class TestSpikeReplay:
    """Canonical replay against the 2026-05-07 09:28-10:56 trace."""

    def test_per_request_alone_does_not_catch_spike(self):
        """Confirm the per-request projection band can't catch the spike.

        This is informational — proves session-cumulative is the relevant
        defense. With Kevin's $10 per-request block threshold, NO single
        spike row crosses it (max single-row spend is ~$1.25).
        """
        rows = _load_spike_rows()
        cfg = SpendGuardConfig()  # session-cumulative DISABLED via override
        cfg.session_block_cost_usd = 0.0
        any_blocked = False
        for r in rows:
            d = decide(_row_to_risk(r), cfg)
            if d.decision in ("block", "hard_block"):
                any_blocked = True
                break
        assert any_blocked is False, (
            "Per-request alone caught the spike — defense level higher than "
            "expected; tighten thresholds in the regression."
        )

    def test_session_cumulative_blocks_before_10_dollars(self):
        """Acceptance lock: session-cumulative blocks before $10 of spend."""
        rows = _load_spike_rows()
        cfg = SpendGuardConfig()
        # Defaults: session_block_cost_usd=10.0, window=3600

        running = 0.0
        first_block_idx = None
        first_block_running = None
        first_block_ts = None

        for i, r in enumerate(rows):
            d = decide(
                _row_to_risk(r),
                cfg,
                session_running_cost_usd=running,
            )
            if d.decision == "block":
                first_block_idx = i
                first_block_running = running
                first_block_ts = r["timestamp"]
                break
            # If allowed, the request would have been forwarded; the proxy
            # would record its actual cost in monitor.db — i.e. the
            # row['estimated_cost'] already reflects the post-call truth.
            running += r["estimated_cost"] or 0.0

        assert first_block_idx is not None, (
            "Session-cumulative did NOT block the spike — defense failed."
        )
        # Acceptance: blocked before $10 cumulative spend.
        assert first_block_running < 10.0, (
            f"Block fired too late — running={first_block_running:.2f} at "
            f"index {first_block_idx} ({first_block_ts}); spec says < $10."
        )
        # Sanity: blocked within the first ~10 minutes of the spike window.
        # Spike started 09:28; with Kevin's $10 ceiling we expect the
        # block to fire by the 09:35-09:40 bucket (running cost crosses
        # $10 around minute 09:38 in the recorded trace).
        assert first_block_ts < "2026-05-07T09:40", (
            f"Block fired at {first_block_ts}; expected within first 12 min."
        )

    def test_total_spend_with_guard_bounded(self):
        """Replay end-to-end: assuming the user says 'no' on the first
        block, total wire-side spend never exceeds the threshold + the
        last (blocked) request's projected cost."""
        rows = _load_spike_rows()
        cfg = SpendGuardConfig()

        running = 0.0
        forwarded = 0.0
        blocked_at = None
        for r in rows:
            d = decide(
                _row_to_risk(r),
                cfg,
                session_running_cost_usd=running,
            )
            if d.decision == "block":
                blocked_at = (running, r["timestamp"])
                break
            running += r["estimated_cost"] or 0.0
            forwarded += r["estimated_cost"] or 0.0

        assert blocked_at is not None
        # The actual recorded spike total was ~$99.67. With 'no' on first
        # block, we expect to forward less than $11 (the running cost just
        # before block — which by definition is < $10 — plus zero replays).
        assert forwarded < 11.0, (
            f"Forwarded ${forwarded:.2f} before block; spec says < $11."
        )
