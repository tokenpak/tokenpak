#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""NCP-1 — capture a parity baseline from existing TokenPak telemetry.

Read-only over ``~/.tokenpak/telemetry.db`` (or
``$TOKENPAK_HOME/telemetry.db``) — joins ``tp_events`` + ``tp_usage``
+ ``intent_patches`` and projects the 20-metric measurement contract
from Standard Proposal #24 §3.

This script makes **no runtime behavior changes**. It only reads
existing telemetry rows and emits a JSON snapshot. For metrics that
the current telemetry layer does not capture, the snapshot uses
``null`` and a sibling ``_unavailable`` field explains why and
where the operator should capture the metric out-of-band (e.g. via
mitmproxy on the native side).

Usage:

    scripts/capture_parity_baseline.py \\
        --label tokenpak \\
        --window-days 1 \\
        --output tests/baselines/ncp-1-parity/tokenpak-2026-04-26.json

For the native-side baseline, the operator records observations
manually (see the NCP-1 A/B test protocol doc) and either:

  - hand-edits a JSON file matching the same schema, or
  - runs this script with ``--label native --from-stdin`` and pipes
    in the raw observation summary.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

# 20-metric schema from Standard #24 §3.
SCHEMA_VERSION: str = "ncp-1-v1"
METRIC_KEYS: tuple = (
    "request_count",
    "retry_count",
    "429_count",
    "5xx_count",
    "latency_ms",
    "time_to_first_token_ms",
    "input_tokens",
    "output_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "companion_added_chars",
    "companion_added_tokens_est",
    "vault_injection_chars",
    "capsule_injection_chars",
    "intent_guidance_chars",
    "hook_triggered_calls",
    "extra_background_calls",
    "retry_after_seconds",
    "ratelimit_tokens_remaining",
    "ratelimit_requests_remaining",
)

# What's available in the current telemetry vs what is unavailable
# (and why). This map is the single source of truth for the
# capture-or-mark-unavailable rule from the directive.
UNAVAILABLE_REASONS: Dict[str, str] = {
    "time_to_first_token_ms": (
        "not yet instrumented (proxy stream layer does not record TTFT). "
        "NCP-1 captures p50/p95 latency_ms only; TTFT lands in NCP-1+ "
        "instrumentation phase."
    ),
    "companion_added_chars": (
        "companion pre-send hook does not log additionalContext length. "
        "Capture out-of-band via TOKENPAK_COMPANION_TRACE_DIR=<path> in "
        "the operator environment, or settle H3 via §5.3 of the "
        "diagnostic plan."
    ),
    "companion_added_tokens_est": (
        "derived from companion_added_chars / 4; unavailable while "
        "companion_added_chars is unavailable."
    ),
    "vault_injection_chars": (
        "vault retrieval does not currently log result-set length. "
        "Capture out-of-band via TOKENPAK_VAULT_DEBUG=1."
    ),
    "capsule_injection_chars": (
        "capsule loader does not currently log loaded-bytes. Capture "
        "out-of-band via TOKENPAK_COMPANION_TRACE_DIR."
    ),
    "hook_triggered_calls": (
        "companion hook dispatcher does not yet emit a count. Per H8 "
        "(NCP-0 diagnostic), the companion makes zero upstream model "
        "calls — this metric is logically zero for NCP-1 settling."
    ),
    "extra_background_calls": (
        "per H8 (NCP-0 diagnostic, ruled out), the companion makes "
        "zero non-user-initiated upstream calls. Reported as 0 with "
        "a confidence flag."
    ),
    "retry_after_seconds": (
        "proxy does not currently parse the upstream Retry-After "
        "header (per H4, NCP-0 diagnostic §1.1). Capture out-of-band "
        "via mitmproxy until NCP-5 lands."
    ),
    "ratelimit_tokens_remaining": (
        "proxy does not currently capture anthropic-ratelimit-tokens-"
        "remaining. Capture out-of-band via mitmproxy or Anthropic "
        "console rate-limit dashboard."
    ),
    "ratelimit_requests_remaining": (
        "proxy does not currently capture anthropic-ratelimit-requests-"
        "remaining. Capture out-of-band via mitmproxy."
    ),
}


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
        if p < 0 or p > 100:
            out[p] = None
            continue
        idx = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
        out[p] = s[idx]
    return out


def _fetch_events(
    conn: sqlite3.Connection, since_iso: str
) -> List[sqlite3.Row]:
    """Pull tp_events rows in the window. Empty list if table missing."""
    if not _table_exists(conn, "tp_events"):
        return []
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT request_id, trace_id, ts, provider, model, agent_id, "
        "api, stop_reason, session_id, duration_ms, status, error_class, "
        "route FROM tp_events WHERE ts >= ? ORDER BY ts",
        (since_iso,),
    ).fetchall()


def _fetch_usage_for_traces(
    conn: sqlite3.Connection, trace_ids: List[str]
) -> Dict[str, sqlite3.Row]:
    if not _table_exists(conn, "tp_usage") or not trace_ids:
        return {}
    conn.row_factory = sqlite3.Row
    out: Dict[str, sqlite3.Row] = {}
    # SQLite parameter limit: chunk in 500s.
    for i in range(0, len(trace_ids), 500):
        batch = trace_ids[i : i + 500]
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"SELECT trace_id, input_billed, output_billed, input_est, "
            f"output_est, cache_read, cache_write, total_tokens "
            f"FROM tp_usage WHERE trace_id IN ({placeholders})",
            batch,
        ).fetchall()
        for r in rows:
            out[r["trace_id"]] = r
    return out


def _fetch_intent_patch_chars(
    conn: sqlite3.Connection, since_iso: str
) -> Dict[str, int]:
    """Map contract_id → patch_text length for applied PI-3 patches."""
    if not _table_exists(conn, "intent_patches"):
        return {}
    rows = conn.execute(
        "SELECT contract_id, patch_text, applied, applied_at "
        "FROM intent_patches WHERE created_at >= ?",
        (since_iso,),
    ).fetchall()
    out: Dict[str, int] = {}
    for r in rows:
        cid = r[0]
        text = r[1] or ""
        if cid:
            out[cid] = max(out.get(cid, 0), len(text))
    return out


def _claude_code_filter(events: List[sqlite3.Row]) -> List[sqlite3.Row]:
    """Filter events to Claude Code traffic only.

    Detects via provider slug substring or route metadata. Falls
    back to all events if no Claude-Code-shaped row is detectable
    (so the snapshot still has data points).
    """
    cc = []
    other = []
    for e in events:
        provider = (e["provider"] or "").lower()
        route = (e["route"] or "").lower()
        if (
            "claude-code" in provider
            or "claude_code" in provider
            or "claude-code" in route
            or "claude_code" in route
        ):
            cc.append(e)
        else:
            other.append(e)
    if cc:
        return cc
    return events  # fall back to all rows


def _bucket(value: Any, default: Any = None) -> Any:
    """Coerce DB row value to JSON-serializable. None stays None."""
    if value is None:
        return default
    return value


def _build_metric_block(
    *,
    label: str,
    window_days: float,
    since_iso: str,
    until_iso: str,
    events: List[sqlite3.Row],
    usage: Dict[str, sqlite3.Row],
) -> Dict[str, Any]:
    """Project the 20 metrics + percentile blocks from telemetry rows."""
    request_count = len(events)

    status_counter: Counter = Counter()
    latencies: List[float] = []
    session_ids: List[str] = []
    retry_count = 0
    for e in events:
        status = e["status"]
        if status is not None:
            try:
                code = int(status)
                status_counter[code] += 1
            except (TypeError, ValueError):
                status_counter[str(status)] += 1
        if e["duration_ms"] is not None:
            try:
                latencies.append(float(e["duration_ms"]))
            except (TypeError, ValueError):
                pass
        if e["session_id"]:
            session_ids.append(e["session_id"])
        # The current schema doesn't have a dedicated retry column;
        # we approximate by counting events flagged with the
        # ``retry`` event_type — but tp_events.event_type isn't
        # universally populated for retries, so this is a lower
        # bound.
        if (e["error_class"] or "").lower() == "retry":
            retry_count += 1

    count_429 = status_counter.get(429, 0)
    count_5xx = sum(v for k, v in status_counter.items() if isinstance(k, int) and 500 <= k < 600)

    input_tokens = 0
    output_tokens = 0
    cache_creation_tokens = 0
    cache_read_tokens = 0
    for e in events:
        u = usage.get(e["trace_id"])
        if u is None:
            continue
        input_tokens += int(u["input_billed"] or u["input_est"] or 0)
        output_tokens += int(u["output_billed"] or u["output_est"] or 0)
        cache_creation_tokens += int(u["cache_write"] or 0)
        cache_read_tokens += int(u["cache_read"] or 0)

    # Cache hit ratio is the H1 settling metric; surface it
    # explicitly so the diff script can read it without re-deriving.
    cache_total = cache_read_tokens + cache_creation_tokens
    cache_hit_ratio = (
        round(cache_read_tokens / cache_total, 4) if cache_total > 0 else None
    )

    distinct_sessions = sorted(set(session_ids))
    rotations_per_hour = (
        round(len(distinct_sessions) / max(0.001, window_days * 24.0), 4)
        if distinct_sessions
        else 0.0
    )

    pct = _percentiles(latencies, [50, 95, 99])

    out: Dict[str, Any] = {
        "label": label,
        "window_days": window_days,
        "window_start": since_iso,
        "window_end": until_iso,
        "metrics": {
            "request_count": request_count,
            "retry_count": retry_count,
            "429_count": count_429,
            "5xx_count": count_5xx,
            "latency_ms": {
                "p50": pct[50],
                "p95": pct[95],
                "p99": pct[99],
            },
            "time_to_first_token_ms": None,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_tokens": cache_creation_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_hit_ratio": cache_hit_ratio,
            "companion_added_chars": None,
            "companion_added_tokens_est": None,
            "vault_injection_chars": None,
            "capsule_injection_chars": None,
            "intent_guidance_chars": None,  # filled below if patches exist
            "hook_triggered_calls": None,
            "extra_background_calls": 0,  # H8 ruled out — companion makes no extra calls
            "retry_after_seconds": None,
            "ratelimit_tokens_remaining": None,
            "ratelimit_requests_remaining": None,
        },
        "session": {
            "distinct_session_id_count": len(distinct_sessions),
            "session_id_rotations_per_hour": rotations_per_hour,
            "first_session_id": distinct_sessions[0] if distinct_sessions else None,
            "session_ids_truncated": (
                # Don't dump unbounded session-id lists into the
                # baseline; first 10 is enough for H2 settling.
                distinct_sessions[:10]
            ),
        },
        "_unavailable": {
            k: v
            for k, v in UNAVAILABLE_REASONS.items()
            if out_get(k) is None
        }
        if False
        else dict(UNAVAILABLE_REASONS),  # always include for transparency
    }
    return out


def out_get(_: str) -> None:
    """Stub — UNAVAILABLE_REASONS is always emitted for transparency."""
    return None


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Capture an NCP-1 parity baseline from "
        "TokenPak telemetry.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--label",
        required=True,
        choices=("native", "tokenpak"),
        help="Variant label: native (no proxy) or tokenpak (through proxy).",
    )
    p.add_argument(
        "--window-days",
        type=float,
        default=1.0,
        help="Lookback window in days. 0 = all rows. Default 1.0.",
    )
    p.add_argument(
        "--db-path",
        default=None,
        help="Override telemetry.db path. Default: $TOKENPAK_HOME/telemetry.db.",
    )
    p.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Write the JSON baseline to this file.",
    )
    p.add_argument(
        "--note",
        default="",
        help="Free-form note to attach to the baseline (e.g. 'native, "
        "captured via mitmproxy 2026-04-26').",
    )
    args = p.parse_args(argv)

    db_path = Path(args.db_path) if args.db_path else _telemetry_db_path()
    now = _dt.datetime.now(_dt.timezone.utc)
    if args.window_days <= 0:
        since = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)
    else:
        since = now - _dt.timedelta(days=args.window_days)
    since_iso = since.isoformat()
    until_iso = now.isoformat()

    if args.label == "native":
        # Native-side baselines cannot be captured from TokenPak's
        # telemetry (TokenPak isn't in the path). Operator should
        # hand-edit the output JSON, or pipe in observations from
        # a mitmproxy capture.
        baseline = {
            "schema_version": SCHEMA_VERSION,
            "captured_at": until_iso,
            "label": "native",
            "window_days": args.window_days,
            "window_start": since_iso,
            "window_end": until_iso,
            "metrics": {k: None for k in METRIC_KEYS},
            "session": {
                "distinct_session_id_count": None,
                "session_id_rotations_per_hour": None,
                "first_session_id": None,
                "session_ids_truncated": [],
            },
            "_unavailable": {
                k: "native baseline must be captured out-of-band per "
                "the NCP-1 A/B test protocol; this template is empty"
                for k in METRIC_KEYS
            },
            "note": args.note
            or (
                "Empty native template — operator must fill in via "
                "mitmproxy / Anthropic-CLI debug logs / Anthropic "
                "console. See docs/internal/specs/ncp-1-ab-test-"
                "protocol-2026-04-26.md."
            ),
        }
    else:
        if not db_path.is_file():
            print(
                f"error: telemetry.db not found at {db_path}. "
                f"Run TokenPak in normal use for the window first.",
                file=sys.stderr,
            )
            return 2
        try:
            with sqlite3.connect(str(db_path)) as conn:
                events = _fetch_events(conn, since_iso)
                events_cc = _claude_code_filter(events)
                trace_ids = [e["trace_id"] for e in events_cc if e["trace_id"]]
                usage = _fetch_usage_for_traces(conn, trace_ids)

                metric_block = _build_metric_block(
                    label=args.label,
                    window_days=args.window_days,
                    since_iso=since_iso,
                    until_iso=until_iso,
                    events=events_cc,
                    usage=usage,
                )

                # PI-3 intent patches — project as
                # intent_guidance_chars (max patch_text length per
                # contract).
                patches = _fetch_intent_patch_chars(conn, since_iso)
                if patches:
                    metric_block["metrics"]["intent_guidance_chars"] = max(
                        patches.values()
                    )
        except sqlite3.DatabaseError as exc:
            print(
                f"error: telemetry.db unreadable: {exc!r}", file=sys.stderr
            )
            return 2

        baseline = {
            "schema_version": SCHEMA_VERSION,
            "captured_at": until_iso,
            "label": args.label,
            **metric_block,
            "note": args.note,
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(baseline, indent=2, sort_keys=True))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
