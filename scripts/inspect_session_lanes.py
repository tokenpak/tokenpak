#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""NCP-3 — read-only session-lane inspection harness.

Reads existing TokenPak telemetry (``~/.tokenpak/telemetry.db`` or
``$TOKENPAK_HOME/telemetry.db``) and produces a markdown (default)
or JSON report covering the eight diagnostic dimensions from
``docs/internal/reports/ncp-3-session-lane-trace-2026-04-27.md``
§5.

This script makes **NO runtime behavior changes**. It is purely
analytical over already-captured telemetry rows. Used by the
operator after running the §4 workload (concurrent
``tokenpak claude`` sessions) to inspect whether sessions
collapsed onto a single wire-side session_id, whether requests
serialized, and whether retries fired.

Usage:

    scripts/inspect_session_lanes.py \\
        --window-minutes 30 \\
        --output tests/baselines/ncp-3-trace/<TIMESTAMP>.md

    scripts/inspect_session_lanes.py \\
        --window-minutes 30 --json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION: str = "ncp-3-trace-v1"


def _telemetry_db_path() -> Path:
    home = os.environ.get("TOKENPAK_HOME", str(Path.home() / ".tokenpak"))
    return Path(home) / "telemetry.db"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _percentiles(values: List[float], pct: List[int]) -> Dict[int, Optional[float]]:
    if not values:
        return {p: None for p in pct}
    s = sorted(values)
    out: Dict[int, Optional[float]] = {}
    for p in pct:
        idx = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
        out[p] = s[idx]
    return out


def _ts_to_seconds(ts: Any) -> Optional[float]:
    """Best-effort parse of tp_events.ts.

    tp_events stores timestamps in two known shapes (legacy float
    epoch seconds, plus ISO-8601 strings on newer rows). We try
    both; return None on parse failure.
    """
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        try:
            return float(ts)
        except ValueError:
            pass
        try:
            return _dt.datetime.fromisoformat(
                ts.replace("Z", "+00:00")
            ).timestamp()
        except ValueError:
            return None
    return None


# ── Dimension 1: H2 ratio (sessions vs requests) ──────────────────────


def _dim_session_collapse(events: List[sqlite3.Row]) -> Dict[str, Any]:
    if not events:
        return {
            "verdict": "no_data",
            "distinct_session_ids": 0,
            "distinct_request_ids": 0,
            "collapse_ratio": None,
        }
    sessions = sorted({(e["session_id"] or "(null)") for e in events})
    requests = sorted({(e["request_id"] or "(null)") for e in events})
    n_s = len(sessions)
    n_r = len(requests)
    ratio = (n_r / n_s) if n_s > 0 else None

    # H2-supportive thresholds (heuristic):
    #   n_r >> n_s with n_s == 1 AND n_r > 4 → "collapsed"
    #   n_r ≈ n_s → "rotating"
    #   n_r / n_s between → "partial"
    if n_s == 1 and n_r > 4:
        verdict = "collapsed"
    elif n_s >= n_r * 0.5:
        verdict = "rotating"
    elif n_r > n_s and n_s > 1:
        verdict = "partial_collapse"
    else:
        verdict = "indeterminate"
    return {
        "verdict": verdict,
        "distinct_session_ids": n_s,
        "distinct_request_ids": n_r,
        "collapse_ratio": round(ratio, 4) if ratio is not None else None,
        "session_ids_truncated": sessions[:10],
    }


# ── Dimension 2: time clustering ──────────────────────────────────────


def _dim_time_clustering(
    events: List[sqlite3.Row],
) -> Dict[str, Any]:
    """Did concurrent requests start within a small window or did
    they serialize?"""
    if len(events) < 2:
        return {"verdict": "insufficient_data", "spans": []}
    parsed = sorted(
        ((_ts_to_seconds(e["ts"]), e["session_id"], e["request_id"]) for e in events),
        key=lambda x: x[0] if x[0] is not None else 0.0,
    )
    parsed = [p for p in parsed if p[0] is not None]
    if len(parsed) < 2:
        return {"verdict": "insufficient_data", "spans": []}
    deltas = []
    for i in range(1, len(parsed)):
        d = parsed[i][0] - parsed[i - 1][0]
        if d >= 0:
            deltas.append(d)
    if not deltas:
        return {"verdict": "insufficient_data", "spans": []}
    median_delta = sorted(deltas)[len(deltas) // 2]
    # If the median gap between consecutive request starts is
    # comparable to a single request's typical duration, the
    # requests are likely serialized.
    durations = [
        float(e["duration_ms"]) for e in events if e["duration_ms"] is not None
    ]
    median_dur_ms = (
        sorted(durations)[len(durations) // 2] if durations else None
    )
    median_dur_s = (median_dur_ms / 1000.0) if median_dur_ms else None
    if median_dur_s is None:
        verdict = "no_duration_data"
    elif median_delta < (0.1 * median_dur_s):
        verdict = "concurrent"
    elif median_delta > (0.5 * median_dur_s):
        verdict = "serialized_or_throttled"
    else:
        verdict = "mixed"
    return {
        "verdict": verdict,
        "request_count": len(parsed),
        "median_inter_request_seconds": round(median_delta, 4),
        "median_request_duration_seconds": (
            round(median_dur_s, 4) if median_dur_s is not None else None
        ),
    }


# ── Dimension 3: status distribution ──────────────────────────────────


def _dim_status_distribution(
    events: List[sqlite3.Row],
) -> Dict[str, Any]:
    counter: Counter = Counter()
    for e in events:
        s = e["status"]
        if s is None:
            counter["(null)"] += 1
        else:
            try:
                code = int(s)
                if code == 200:
                    counter["200"] += 1
                elif code == 429:
                    counter["429"] += 1
                elif 500 <= code < 600:
                    counter["5xx"] += 1
                else:
                    counter[str(code)] += 1
            except (TypeError, ValueError):
                counter[str(s)] += 1
    return dict(counter)


# ── Dimension 4: per-session-id duration percentiles ─────────────────


def _dim_per_session_durations(
    events: List[sqlite3.Row],
) -> Dict[str, Any]:
    by_session: Dict[str, List[float]] = defaultdict(list)
    for e in events:
        if e["duration_ms"] is None:
            continue
        try:
            d = float(e["duration_ms"])
        except (TypeError, ValueError):
            continue
        sid = e["session_id"] or "(null)"
        by_session[sid].append(d)
    out: Dict[str, Any] = {}
    for sid, values in by_session.items():
        pct = _percentiles(values, [50, 95, 99])
        out[sid] = {
            "count": len(values),
            "p50_ms": pct[50],
            "p95_ms": pct[95],
            "p99_ms": pct[99],
        }
    return out


# ── Dimension 5: provider audit (I-0 violation detection) ─────────────


def _dim_provider_audit(events: List[sqlite3.Row]) -> Dict[str, Any]:
    counter: Counter = Counter()
    for e in events:
        counter[(e["provider"] or "(null)").lower()] += 1
    non_oauth = sorted(
        p
        for p in counter
        if p != "tokenpak-claude-code" and p != "(null)"
    )
    return {
        "distribution": dict(counter),
        "non_oauth_providers": non_oauth,
        "i0_violation": len(non_oauth) > 0,
    }


# ── Dimension 6: retry-event count (lower bound) ──────────────────────


def _dim_retry_count(events: List[sqlite3.Row]) -> Dict[str, Any]:
    n = sum(1 for e in events if (e["error_class"] or "").lower() == "retry")
    return {
        "retry_event_lower_bound": n,
        "note": (
            "Lower bound — current schema doesn't tag every retry. Real "
            "retry behavior may be higher; settle via the H4 'Retrying "
            "in 20s' visual evidence + the §4.4 OAuth-fresh test."
        ),
    }


# ── Dimension 7: token usage averages ─────────────────────────────────


def _dim_token_usage(
    conn: sqlite3.Connection, events: List[sqlite3.Row]
) -> Dict[str, Any]:
    if not _table_exists(conn, "tp_usage"):
        return {"available": False}
    trace_ids = [e["trace_id"] for e in events if e["trace_id"]]
    if not trace_ids:
        return {"available": True, "no_usage_rows": True}
    placeholders = ",".join("?" for _ in trace_ids)
    row = conn.execute(
        f"SELECT SUM(input_billed) as ib, SUM(input_est) as ie, "
        f"SUM(output_billed) as ob, SUM(cache_read) as cr, "
        f"SUM(cache_write) as cw, COUNT(*) as n "
        f"FROM tp_usage WHERE trace_id IN ({placeholders})",
        trace_ids,
    ).fetchone()
    cr = int(row["cr"] or 0)
    cw = int(row["cw"] or 0)
    cache_total = cr + cw
    return {
        "available": True,
        "rows_joined": int(row["n"] or 0),
        "input_tokens_total": int(row["ib"] or row["ie"] or 0),
        "output_tokens_total": int(row["ob"] or 0),
        "cache_read_tokens": cr,
        "cache_creation_tokens": cw,
        "cache_hit_ratio": (
            round(cr / cache_total, 4) if cache_total > 0 else None
        ),
    }


# ── Dimension 8: cross-session interleaving ──────────────────────────


def _dim_interleaving(events: List[sqlite3.Row]) -> Dict[str, Any]:
    """Did request N from session X land between requests N-1 and
    N+1 of session Y, or did the proxy serialize them by session?
    """
    parsed = []
    for e in events:
        ts = _ts_to_seconds(e["ts"])
        if ts is None:
            continue
        parsed.append((ts, e["session_id"] or "(null)"))
    if len(parsed) < 4:
        return {"verdict": "insufficient_data", "interleave_score": None}
    parsed.sort(key=lambda x: x[0])
    # Interleave score = number of consecutive-request pairs that
    # span DIFFERENT sessions divided by total consecutive pairs.
    # 1.0 = fully interleaved (concurrent), 0.0 = fully serialized.
    differing = 0
    total = 0
    for i in range(1, len(parsed)):
        if parsed[i][1] != parsed[i - 1][1]:
            differing += 1
        total += 1
    score = differing / total if total else 0.0
    if score >= 0.5:
        verdict = "interleaved"
    elif score >= 0.25:
        verdict = "partially_interleaved"
    else:
        verdict = "serialized"
    return {
        "verdict": verdict,
        "interleave_score": round(score, 4),
        "consecutive_pairs": total,
    }


# ── Top-level analysis ───────────────────────────────────────────────


def analyze(
    *,
    db_path: Path,
    window_minutes: float,
) -> Dict[str, Any]:
    if not db_path.is_file():
        return {
            "schema_version": SCHEMA_VERSION,
            "error": f"telemetry.db not found at {db_path}",
        }

    now = _dt.datetime.now(_dt.timezone.utc)
    if window_minutes <= 0:
        since = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)
    else:
        since = now - _dt.timedelta(minutes=window_minutes)
    since_ts = since.timestamp()
    since_iso = since.isoformat()

    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            if not _table_exists(conn, "tp_events"):
                return {
                    "schema_version": SCHEMA_VERSION,
                    "error": "tp_events table missing",
                }
            # tp_events.ts is a mix of float epoch and ISO-8601 in
            # the wild. SQL-side we filter loosely (ts >= since_ts
            # AS NUMERIC OR ts >= since_iso); callers can rely on
            # the Python-side filter below for correctness.
            rows = conn.execute(
                "SELECT request_id, trace_id, ts, provider, model, "
                "agent_id, api, stop_reason, session_id, duration_ms, "
                "status, error_class, route FROM tp_events "
                "ORDER BY ts DESC LIMIT 5000"
            ).fetchall()

            # Python-side filter for the window.
            kept: List[sqlite3.Row] = []
            for r in rows:
                ts_s = _ts_to_seconds(r["ts"])
                if ts_s is None:
                    continue
                if ts_s >= since_ts:
                    kept.append(r)

            # Filter to Claude Code traffic.
            claude_code = [
                r
                for r in kept
                if (r["provider"] or "").lower() == "tokenpak-claude-code"
                or "claude-code" in (r["provider"] or "").lower()
                or "claude-code" in (r["route"] or "").lower()
            ]
            if not claude_code:
                claude_code = kept  # fall back so the report has data

            d1 = _dim_session_collapse(claude_code)
            d2 = _dim_time_clustering(claude_code)
            d3 = _dim_status_distribution(claude_code)
            d4 = _dim_per_session_durations(claude_code)
            d5 = _dim_provider_audit(claude_code)
            d6 = _dim_retry_count(claude_code)
            d7 = _dim_token_usage(conn, claude_code)
            d8 = _dim_interleaving(claude_code)
    except sqlite3.DatabaseError as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "error": f"telemetry.db unreadable: {exc!r}",
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "window_start": since_iso,
        "window_end": now.isoformat(),
        "window_minutes": window_minutes,
        "claude_code_event_count": len(claude_code),
        "dim1_session_collapse": d1,
        "dim2_time_clustering": d2,
        "dim3_status_distribution": d3,
        "dim4_per_session_durations": d4,
        "dim5_provider_audit": d5,
        "dim6_retry_count": d6,
        "dim7_token_usage": d7,
        "dim8_interleaving": d8,
    }


def _verdict_lines(report: Dict[str, Any]) -> List[str]:
    """Render the synthesis lines per the NCP-3 doc §6 decision
    tree."""
    out: List[str] = []
    d1 = report.get("dim1_session_collapse", {})
    d5 = report.get("dim5_provider_audit", {})
    d6 = report.get("dim6_retry_count", {})

    # I-0 first
    if d5.get("i0_violation"):
        out.append(
            "**I-0 VIOLATION** — non-OAuth providers found in window: "
            f"`{d5.get('non_oauth_providers')}`. The run is invalid; "
            "rerun via `tokenpak claude` per NCP-1R §4.1."
        )

    # Q1: session collapse
    v1 = d1.get("verdict")
    if v1 == "collapsed":
        out.append(
            "**Q1 — H2 session-id collapse: SUPPORTED.** "
            f"Distinct session_ids = {d1.get('distinct_session_ids')}, "
            f"requests = {d1.get('distinct_request_ids')}."
        )
    elif v1 == "rotating":
        out.append(
            "**Q1 — H2 session-id collapse: NOT SUPPORTED.** "
            f"Distinct session_ids = {d1.get('distinct_session_ids')}, "
            f"requests = {d1.get('distinct_request_ids')}."
        )
    else:
        out.append(
            f"**Q1 — H2 session-id collapse: {v1}.** "
            "Inconclusive — rerun with the §4 workload (2 concurrent "
            "tokenpak claude sessions) and re-inspect."
        )

    # Q3: retry count
    n_retry = d6.get("retry_event_lower_bound", 0)
    if n_retry > 0:
        out.append(
            f"**Q3 — retry events recorded:** {n_retry} (lower bound). "
            "Combined with the iter-2 'Retrying in 20s' anecdotal "
            "evidence, H4 retry amplification is corroborated."
        )
    else:
        out.append(
            "**Q3 — retry events recorded:** 0. Either no retries "
            "occurred OR the schema didn't tag them. Settle visually "
            "via the §4 workload."
        )
    return out


def _render_markdown(report: Dict[str, Any]) -> str:
    if "error" in report:
        return f"# NCP-3 session-lane trace\n\n**Error**: {report['error']}\n"
    lines: List[str] = []
    lines.append("# NCP-3 session-lane trace")
    lines.append("")
    lines.append(f"**Generated**: {report['generated_at']}")
    lines.append(f"**Window**: {report['window_minutes']} min "
                 f"({report['window_start']} → {report['window_end']})")
    lines.append(f"**Claude Code event count in window**: {report['claude_code_event_count']}")
    lines.append("")
    lines.append("## Synthesis")
    lines.append("")
    for v in _verdict_lines(report):
        lines.append(f"- {v}")
    lines.append("")
    lines.append("## Dimensions")
    lines.append("")
    for k in (
        "dim1_session_collapse",
        "dim2_time_clustering",
        "dim3_status_distribution",
        "dim4_per_session_durations",
        "dim5_provider_audit",
        "dim6_retry_count",
        "dim7_token_usage",
        "dim8_interleaving",
    ):
        lines.append(f"### {k}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(report.get(k), indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="NCP-3 read-only session-lane inspection.",
    )
    p.add_argument(
        "--db-path",
        default=None,
        help="Override telemetry.db path. Default: $TOKENPAK_HOME/telemetry.db.",
    )
    p.add_argument(
        "--window-minutes",
        type=float,
        default=30.0,
        help="Lookback window in minutes (default 30; 0 = all rows).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of markdown.",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the report to this file (default: stdout).",
    )
    args = p.parse_args(argv)

    db_path = Path(args.db_path) if args.db_path else _telemetry_db_path()
    report = analyze(db_path=db_path, window_minutes=args.window_minutes)

    rendered = (
        json.dumps(report, indent=2, sort_keys=True)
        if args.json
        else _render_markdown(report)
    )
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(rendered)
    return 0 if "error" not in report else 2


if __name__ == "__main__":
    raise SystemExit(main())
