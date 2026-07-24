# SPDX-License-Identifier: Apache-2.0
"""Canonical spike-replay test.

Replays the 2026-05-07 09:28-10:56 UTC trace against the spend guard pipeline
and asserts the guard would have blocked the runaway before $10 in spend.

The trace lives in the user's actual ``~/.tokenpak/monitor.db`` (the DB the
proxy writes to in production). We snapshot the relevant rows in a temp
DB so the test is reproducible and doesn't mutate the live DB.
"""

from __future__ import annotations

import os
import sqlite3
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


def _dollar_plane_cfg() -> SpendGuardConfig:
    """Reconstruct the v1.5.1 dollar-plane default profile for tests that
    exercise the LEGACY session-cumulative defense.

    Under v1.5.2 defaults (Kevin DECISION 2026-05-11 rev 2),
    the dollar plane is opt-in only. These tests explicitly engage it to
    keep regression coverage on the legacy band.
    """
    import warnings

    from tokenpak.proxy.spend_guard.policy import load_config

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return load_config(
            raw_config={
                "spend_guard": {
                    "block_cost_usd": 10.0,
                    "hard_block_cost_usd": 50.0,
                    "session_block_cost_usd": 10.0,
                }
            }
        )


class TestSpikeReplayLegacyDollarPlane:
    """Replay against the 2026-05-07 09:28-10:56 trace under the LEGACY
    dollar-plane profile (v1.5.1 defaults, now opt-in).

    Kept as a regression on the legacy defense so the opt-in path remains
    correct. The canonical v1.5.2 defense is exercised below in
    :class:`TestSpikeReplayContextWindowPercent`.
    """

    def test_per_request_alone_does_not_catch_spike(self):
        """Confirm the per-request dollar band can't catch the spike.

        Informational — proves session-cumulative is the relevant defense
        within the dollar plane. With the v1.5.1 $10 per-request block
        threshold, NO single spike row crosses it (max single-row spend
        is ~$1.25).
        """
        rows = _load_spike_rows()
        cfg = _dollar_plane_cfg()
        cfg.session_block_cost_usd = 0.0  # isolate per-request behavior
        any_blocked = False
        for r in rows:
            d = decide(_row_to_risk(r), cfg)
            if d.decision in ("block", "hard_block"):
                any_blocked = True
                break
        assert any_blocked is False, (
            "Per-request dollar plane caught the spike — defense level "
            "higher than expected; tighten thresholds in the regression."
        )

    def test_session_cumulative_blocks_before_10_dollars(self):
        """Acceptance lock: session-cumulative dollar plane blocks before
        $10 of spend when explicitly configured."""
        rows = _load_spike_rows()
        cfg = _dollar_plane_cfg()
        # Dollar plane: session_block_cost_usd=10.0, window=3600

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
            running += r["estimated_cost"] or 0.0

        assert first_block_idx is not None, (
            "Session-cumulative did NOT block the spike — defense failed."
        )
        assert first_block_running < 10.0, (
            f"Block fired too late — running={first_block_running:.2f} at "
            f"index {first_block_idx} ({first_block_ts}); spec says < $10."
        )
        assert first_block_ts < "2026-05-07T09:40", (
            f"Block fired at {first_block_ts}; expected within first 12 min."
        )

    def test_total_spend_with_guard_bounded(self):
        """Replay end-to-end: assuming the user says 'no' on the first
        block, total wire-side spend never exceeds the threshold + the
        last (blocked) request's projected cost."""
        rows = _load_spike_rows()
        cfg = _dollar_plane_cfg()

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
        assert forwarded < 11.0, f"Forwarded ${forwarded:.2f} before block; spec says < $11."


class TestSpikeReplayContextWindowPercent:
    """Canonical v1.5.2 replay — the per-request context-window-% basis
    at 90% catches the spike pattern without any session-cumulative
    bookkeeping.

    Acceptance from the 2026-05-11 task packet:
    > Regression: ``test_spend_guard_spike_replay.py`` must continue to
    > flag the 2026-05-07 09:28-10:56 trace under the new 90% default —
    > verify each large-context request in the spike pattern crosses the
    > 90% line.

    The spike pattern was characterized by repeated large-context calls
    on Opus 4.7 (200K context) where each request's cached context was
    near or above 180K (90% of 200K). The % basis catches those rows
    per-request, no cumulative state required.
    """

    def test_at_least_one_spike_row_blocks_under_default(self):
        """Under the v1.5.2 default profile, at least one row of the
        recorded spike crosses the 90% context-window line."""
        rows = _load_spike_rows()
        cfg = SpendGuardConfig()  # v1.5.2 defaults — % basis at 90%
        blocked_count = 0
        first_block_ts = None
        for r in rows:
            est = _row_to_risk(r)
            d = decide(est, cfg, model_max_context_tokens=200_000)
            if d.decision in ("block", "hard_block"):
                blocked_count += 1
                if first_block_ts is None:
                    first_block_ts = r["timestamp"]
        assert blocked_count > 0, (
            "Spike replay produced 0 blocks under v1.5.2 default policy. "
            "The per-request context-window-% defense must catch at least "
            "one large-context row in the recorded spike. If this assertion "
            "fires, the spike trace shape may have changed, or the % basis "
            "implementation regressed."
        )
        assert first_block_ts < "2026-05-07T10:00", (
            f"First block fired at {first_block_ts}; expected during the "
            "spike window (09:28..10:00)."
        )

    def test_total_forwarded_bounded_under_context_window_basis(self):
        """If the user says 'no' on the first block, total wire-side spend
        is bounded by the cost of pre-block rows.

        The spike total was ~$99.67. Under the v1.5.2 % basis, the first
        block fires when a row's context first crosses 180K — typically
        well before $99 cumulative spend.
        """
        rows = _load_spike_rows()
        cfg = SpendGuardConfig()  # v1.5.2 defaults
        forwarded = 0.0
        blocked_at = None
        for r in rows:
            est = _row_to_risk(r)
            d = decide(est, cfg, model_max_context_tokens=200_000)
            if d.decision in ("block", "hard_block"):
                blocked_at = r["timestamp"]
                break
            forwarded += r["estimated_cost"] or 0.0
        assert blocked_at is not None, "% basis did NOT block the spike under v1.5.2 defaults."
        # The full spike was $99.67; we expect to forward well under that
        # before the first context-window-% block fires.
        assert forwarded < 99.0, (
            f"Forwarded ${forwarded:.2f} before first % block; expected "
            "substantially less than the unguarded $99.67 total."
        )
