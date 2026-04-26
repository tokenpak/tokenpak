#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""NCP-1 — diff two parity baselines and settle H1 / H2.

Reads ``native`` and ``tokenpak`` JSON baselines (as produced by
``capture_parity_baseline.py``) and emits a results report. The
report follows the directive's results template:

  - H1 supported / not supported
  - H2 supported / not supported
  - dominant cause
  - confidence level
  - recommended NCP-2 fix direction

This script makes **no runtime behavior changes**. It is purely a
read-only analytical tool over already-captured baselines.

Usage:

    scripts/diff_parity_baselines.py \\
        --native tests/baselines/ncp-1-parity/native-2026-04-26.json \\
        --tokenpak tests/baselines/ncp-1-parity/tokenpak-2026-04-26.json \\
        [--json] [--output FILE]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Thresholds for H1 / H2 verdict — calibrated against Standard #24
# §4 fail-safe contract. Documented here so a future NCP-1 run can
# tighten or relax them with a one-line edit.
H1_CACHE_HIT_DELTA_THRESHOLD: float = 0.30
"""If TokenPak's cache_hit_ratio is ``H1_CACHE_HIT_DELTA_THRESHOLD``
(absolute) below the native ratio, H1 is "supported"."""

H2_SESSION_ROTATION_RATIO_THRESHOLD: float = 3.0
"""If native's session_id_rotations_per_hour is at least
``H2_SESSION_ROTATION_RATIO_THRESHOLD`` × the TokenPak rotation rate,
H2 is "supported"."""

H2_SESSION_COUNT_RATIO_THRESHOLD: float = 3.0
"""Same idea applied to distinct_session_id_count when the rotation
rate is unavailable."""


# ── Helpers ────────────────────────────────────────────────────────────


def _load(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"error: baseline not found: {path}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: baseline is not valid JSON ({path}): {exc}")


def _pct(num: Optional[float], denom: Optional[float]) -> Optional[float]:
    if num is None or denom is None or denom == 0:
        return None
    return round(num / denom, 4)


def _safe_get(d: Dict[str, Any], *keys: str) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


# ── H1 — cache prefix disruption ───────────────────────────────────────


def _evaluate_h1(
    native: Dict[str, Any], tokenpak: Dict[str, Any]
) -> Dict[str, Any]:
    nat_ratio = _safe_get(native, "metrics", "cache_hit_ratio")
    tok_ratio = _safe_get(tokenpak, "metrics", "cache_hit_ratio")

    nat_creation = _safe_get(native, "metrics", "cache_creation_tokens")
    nat_read = _safe_get(native, "metrics", "cache_read_tokens")
    tok_creation = _safe_get(tokenpak, "metrics", "cache_creation_tokens")
    tok_read = _safe_get(tokenpak, "metrics", "cache_read_tokens")

    if nat_ratio is None or tok_ratio is None:
        return {
            "verdict": "inconclusive",
            "reason": "cache_hit_ratio unavailable on at least one side; "
            "see _unavailable section of the missing baseline. Fill in "
            "and rerun.",
            "native_cache_hit_ratio": nat_ratio,
            "tokenpak_cache_hit_ratio": tok_ratio,
            "native_cache_creation_tokens": nat_creation,
            "native_cache_read_tokens": nat_read,
            "tokenpak_cache_creation_tokens": tok_creation,
            "tokenpak_cache_read_tokens": tok_read,
        }

    delta = nat_ratio - tok_ratio
    supported = delta >= H1_CACHE_HIT_DELTA_THRESHOLD
    return {
        "verdict": "supported" if supported else "not_supported",
        "reason": (
            f"native cache_hit_ratio={nat_ratio:.4f}, tokenpak={tok_ratio:.4f}, "
            f"delta={delta:.4f} "
            f"({'≥' if supported else '<'} {H1_CACHE_HIT_DELTA_THRESHOLD} threshold)"
        ),
        "native_cache_hit_ratio": nat_ratio,
        "tokenpak_cache_hit_ratio": tok_ratio,
        "delta": round(delta, 4),
        "threshold": H1_CACHE_HIT_DELTA_THRESHOLD,
        "native_cache_creation_tokens": nat_creation,
        "native_cache_read_tokens": nat_read,
        "tokenpak_cache_creation_tokens": tok_creation,
        "tokenpak_cache_read_tokens": tok_read,
    }


# ── H2 — session-id collapse ───────────────────────────────────────────


def _evaluate_h2(
    native: Dict[str, Any], tokenpak: Dict[str, Any]
) -> Dict[str, Any]:
    nat_rot = _safe_get(native, "session", "session_id_rotations_per_hour")
    tok_rot = _safe_get(tokenpak, "session", "session_id_rotations_per_hour")
    nat_distinct = _safe_get(native, "session", "distinct_session_id_count")
    tok_distinct = _safe_get(tokenpak, "session", "distinct_session_id_count")

    base = {
        "native_rotations_per_hour": nat_rot,
        "tokenpak_rotations_per_hour": tok_rot,
        "native_distinct_session_count": nat_distinct,
        "tokenpak_distinct_session_count": tok_distinct,
        "rotation_threshold": H2_SESSION_ROTATION_RATIO_THRESHOLD,
        "count_threshold": H2_SESSION_COUNT_RATIO_THRESHOLD,
    }

    if nat_rot is not None and tok_rot is not None and tok_rot > 0:
        ratio = nat_rot / tok_rot
        supported = ratio >= H2_SESSION_ROTATION_RATIO_THRESHOLD
        base["ratio"] = round(ratio, 4)
        base["verdict"] = "supported" if supported else "not_supported"
        base["reason"] = (
            f"native rotations/h={nat_rot:.4f}, tokenpak={tok_rot:.4f}, "
            f"ratio={ratio:.2f}× "
            f"({'≥' if supported else '<'} {H2_SESSION_ROTATION_RATIO_THRESHOLD}× threshold)"
        )
        return base

    if nat_rot is not None and tok_rot == 0 and nat_rot > 0:
        # TokenPak collapsed to zero rotation in the window — strongest
        # form of H2 evidence.
        base["ratio"] = float("inf")
        base["verdict"] = "supported"
        base["reason"] = (
            f"tokenpak rotations/h=0 over the window while native rotated "
            f"{nat_rot:.4f}/h — session-id is fully collapsed"
        )
        return base

    if nat_distinct is not None and tok_distinct is not None and tok_distinct > 0:
        ratio = nat_distinct / tok_distinct
        supported = ratio >= H2_SESSION_COUNT_RATIO_THRESHOLD
        base["count_ratio"] = round(ratio, 4)
        base["verdict"] = "supported" if supported else "not_supported"
        base["reason"] = (
            f"native distinct sessions={nat_distinct}, tokenpak={tok_distinct}, "
            f"ratio={ratio:.2f}× "
            f"({'≥' if supported else '<'} {H2_SESSION_COUNT_RATIO_THRESHOLD}× threshold)"
        )
        return base

    base["verdict"] = "inconclusive"
    base["reason"] = (
        "session-id rotation data unavailable on at least one side; "
        "fill in the native baseline's session block per the protocol "
        "doc and rerun."
    )
    return base


# ── Synthesis ──────────────────────────────────────────────────────────


def _dominant_cause(
    h1: Dict[str, Any], h2: Dict[str, Any]
) -> Dict[str, Any]:
    """Pick the dominant hypothesis from the two verdicts.

    Conservative rule: the synthesis only claims a dominant cause
    when **both** hypotheses have a definite verdict (supported or
    not_supported). Any inconclusive verdict falls through to
    ``inconclusive`` regardless of the other side.
    """
    h1_v = h1.get("verdict")
    h2_v = h2.get("verdict")

    DEFINITE = {"supported", "not_supported"}
    if h1_v not in DEFINITE or h2_v not in DEFINITE:
        return {
            "dominant_cause": "inconclusive",
            "confidence": "low",
            "rationale": (
                "At least one hypothesis verdict is inconclusive — see the "
                "H1 and H2 blocks for the missing data. Fill in the gap and "
                "rerun."
            ),
        }

    if h1_v == "supported" and h2_v == "supported":
        return {
            "dominant_cause": "H1+H2 both supported",
            "confidence": "high",
            "rationale": (
                "Cache prefix disruption AND session-id collapse both fire. "
                "Both contribute; NCP-2 should fix cache prefix preservation "
                "and NCP-3 should fix session-id rotation. Run §5.1 and "
                "§5.2 a second time after each fix to confirm independent "
                "contribution."
            ),
        }
    if h1_v == "supported":
        return {
            "dominant_cause": "H1 — cache prefix disruption",
            "confidence": "high",
            "rationale": (
                "Companion-prepended dynamic content invalidates the "
                "Anthropic prompt-prefix cache. Native variant gets cache "
                "hits on the stable system block; TokenPak variant misses "
                "every request. NCP-2 fix direction: move companion "
                "context BEHIND the cache boundary, or wrap it in a "
                "stable cache-key boundary that the provider honours."
            ),
        }
    if h2_v == "supported":
        return {
            "dominant_cause": "H2 — session-id collapse",
            "confidence": "high",
            "rationale": (
                "Proxy collapses many CLI invocations onto a single "
                "X-Claude-Code-Session-Id. Anthropic attributes rate-limit "
                "consumption to that single id, so the bucket fills "
                "faster than under native CLI rotation. NCP-3 fix "
                "direction: rotate session-id per CLI invocation, or per "
                "K requests / T seconds when invocation boundary cannot "
                "be detected."
            ),
        }
    # Both not_supported.
    return {
        "dominant_cause": "neither H1 nor H2",
        "confidence": "medium",
        "rationale": (
            "Cache hit ratio and session-id rotation are within "
            "thresholds. The observed rate-limit difference is likely "
            "driven by H3 (token amplification), H4 (Retry-After "
            "ignored), or a hypothesis not enumerated in NCP-0. "
            "Capture H3 / H4 evidence per the diagnostic plan §5.3 / "
            "§5.4 and re-run NCP-1."
        ),
    }


def _recommend_fix(
    h1: Dict[str, Any], h2: Dict[str, Any]
) -> List[str]:
    out: List[str] = []
    if h1.get("verdict") == "supported":
        out.append(
            "NCP-2 — Cache prefix preservation. Move companion-added "
            "context behind the cache boundary on byte-preserved "
            "adapters (AnthropicAdapter), or wrap it in a cache-key-"
            "stable boundary. Verify with a second §5.1 A/B run."
        )
    if h2.get("verdict") == "supported":
        out.append(
            "NCP-3 — Session-id rotation. Rotate "
            "X-Claude-Code-Session-Id per Claude Code CLI invocation, "
            "or per K requests / T seconds when the invocation "
            "boundary cannot be detected. Preserve a per-process "
            "stable id only for callers that explicitly opt in (e.g. "
            "OpenClaw billing)."
        )
    if not out:
        out.append(
            "No fix recommended yet — H1/H2 not supported. Capture H3 "
            "(token amplification) and H4 (Retry-After) evidence per "
            "the diagnostic plan and re-run."
        )
    return out


def _render_markdown(report: Dict[str, Any]) -> str:
    h1 = report["h1"]
    h2 = report["h2"]
    syn = report["synthesis"]
    lines: List[str] = []
    lines.append("# NCP-1 parity A/B results")
    lines.append("")
    lines.append(f"**Generated**: {report['generated_at']}")
    lines.append(f"**Native baseline**: `{report['native_path']}`")
    lines.append(f"**TokenPak baseline**: `{report['tokenpak_path']}`")
    lines.append("")
    lines.append("## Verdicts")
    lines.append("")
    lines.append(f"- **H1 (cache prefix disruption)**: `{h1.get('verdict')}` — {h1.get('reason', '')}")
    lines.append(f"- **H2 (session-id collapse)**: `{h2.get('verdict')}` — {h2.get('reason', '')}")
    lines.append("")
    lines.append("## Synthesis")
    lines.append("")
    lines.append(f"- **Dominant cause**: {syn['dominant_cause']}")
    lines.append(f"- **Confidence**: {syn['confidence']}")
    lines.append(f"- **Rationale**: {syn['rationale']}")
    lines.append("")
    lines.append("## Recommended next step")
    lines.append("")
    for rec in report["recommendation"]:
        lines.append(f"- {rec}")
    lines.append("")
    lines.append("## Raw H1 block")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(h1, indent=2, sort_keys=True))
    lines.append("```")
    lines.append("")
    lines.append("## Raw H2 block")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(h2, indent=2, sort_keys=True))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


# ── Entry point ────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Diff two NCP-1 parity baselines.",
    )
    p.add_argument("--native", type=Path, required=True)
    p.add_argument("--tokenpak", type=Path, required=True)
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit the report as JSON instead of markdown.",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the report to this file (default: stdout).",
    )
    args = p.parse_args(argv)

    native = _load(args.native)
    tokenpak = _load(args.tokenpak)

    h1 = _evaluate_h1(native, tokenpak)
    h2 = _evaluate_h2(native, tokenpak)
    syn = _dominant_cause(h1, h2)
    rec = _recommend_fix(h1, h2)

    report = {
        "schema_version": "ncp-1-diff-v1",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "native_path": str(args.native),
        "tokenpak_path": str(args.tokenpak),
        "h1": h1,
        "h2": h2,
        "synthesis": syn,
        "recommendation": rec,
    }

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
