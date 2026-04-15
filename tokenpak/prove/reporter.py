# SPDX-License-Identifier: Apache-2.0
"""Format and display the prove comparison report.

Takes results from both arms and produces:
  - Terminal output with side-by-side comparison table
  - JSON file for programmatic access / later review
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .arm import ArmResult


def format_report(
    arm_a: ArmResult,
    arm_b: ArmResult,
    scenario_name: str,
    proof_id: str,
) -> str:
    """Format the comparison report for terminal display."""
    lines: list[str] = []
    w = 62  # total width

    lines.append("")
    lines.append(f"  TokenPak Value Proof — \"{scenario_name}\"")
    lines.append("  " + "=" * (w - 2))

    # Summary line
    n_turns = max(len(arm_a.turns), len(arm_b.turns))
    lines.append(f"  Model: {arm_a.model}  |  Turns: {n_turns}  |  Provider: {arm_a.provider}")
    lines.append("")

    # Check for errors
    if arm_a.error and not arm_a.turns:
        lines.append(f"  Arm A error: {arm_a.error}")
    if arm_b.error and not arm_b.turns:
        lines.append(f"  Arm B error: {arm_b.error}")
    if (arm_a.error and not arm_a.turns) or (arm_b.error and not arm_b.turns):
        return "\n".join(lines)

    # ── Aggregate comparison ────────────────────────────────
    col_w = 16
    hdr = f"  {'':24s}{'Direct':>{col_w}s}{'TokenPak':>{col_w}s}{'Delta':>{col_w}s}"
    sep = "  " + "-" * (24 + col_w * 3)

    lines.append(hdr)
    lines.append(sep)

    lines.append(_row("Input tokens",
                       f"{arm_a.total_input_tokens:,}",
                       f"{arm_b.total_input_tokens:,}",
                       _pct_delta(arm_a.total_input_tokens, arm_b.total_input_tokens),
                       col_w))

    if arm_b.total_cache_read_tokens:
        lines.append(_row("Cache-read tokens",
                           "0",
                           f"{arm_b.total_cache_read_tokens:,}",
                           "",
                           col_w))

    lines.append(_row("Output tokens",
                       f"{arm_a.total_output_tokens:,}",
                       f"{arm_b.total_output_tokens:,}",
                       _pct_delta(arm_a.total_output_tokens, arm_b.total_output_tokens),
                       col_w))

    lines.append(_row("Total cost",
                       f"${arm_a.total_cost_usd:.4f}",
                       f"${arm_b.total_cost_usd:.4f}",
                       _pct_delta(arm_a.total_cost_usd, arm_b.total_cost_usd),
                       col_w))

    lines.append(_row("Total time",
                       f"{arm_a.total_latency_s:.1f}s",
                       f"{arm_b.total_latency_s:.1f}s",
                       _pct_delta(arm_a.total_latency_s, arm_b.total_latency_s),
                       col_w))

    lines.append(sep)

    # ── Per-turn breakdown ──────────────────────────────────
    lines.append("")
    lines.append("  Per-turn breakdown:")

    for i in range(n_turns):
        ta = arm_a.turns[i] if i < len(arm_a.turns) else None
        tb = arm_b.turns[i] if i < len(arm_b.turns) else None

        label = (ta or tb).label if (ta or tb) else f"Turn {i + 1}"
        lines.append(f"\n  Turn {i + 1}: {label}")

        a_in = ta.input_tokens if ta else 0
        b_in = tb.input_tokens if tb else 0
        lines.append(_row("  Input", f"{a_in:,}", f"{b_in:,}",
                           _pct_delta(a_in, b_in), col_w))

        b_cache = tb.cache_read_tokens if tb else 0
        if b_cache:
            lines.append(_row("  Cached", "0", f"{b_cache:,}", "", col_w))

        a_out = ta.output_tokens if ta else 0
        b_out = tb.output_tokens if tb else 0
        lines.append(_row("  Output", f"{a_out:,}", f"{b_out:,}",
                           _pct_delta(a_out, b_out), col_w))

        a_cost = ta.cost_usd if ta else 0
        b_cost = tb.cost_usd if tb else 0
        lines.append(_row("  Cost", f"${a_cost:.4f}", f"${b_cost:.4f}",
                           _pct_delta(a_cost, b_cost), col_w))

        a_time = ta.latency_s if ta else 0
        b_time = tb.latency_s if tb else 0
        lines.append(_row("  Time", f"{a_time:.1f}s", f"{b_time:.1f}s",
                           _pct_delta(a_time, b_time), col_w))

    # ── Feature attribution ─────────────────────────────────
    if arm_b.total_cache_read_tokens:
        lines.append("")
        lines.append("  Feature attribution:")
        lines.append(f"    cache_control      saved {arm_b.total_cache_read_tokens:,} cache-read tokens")

    input_saved = arm_a.total_input_tokens - arm_b.total_input_tokens
    if input_saved > 0:
        lines.append(f"    compression        saved {input_saved:,} input tokens ({_pct(input_saved, arm_a.total_input_tokens)})")

    cost_saved = arm_a.total_cost_usd - arm_b.total_cost_usd
    if cost_saved > 0:
        lines.append(f"    total_savings      ${cost_saved:.4f} saved ({_pct(cost_saved, arm_a.total_cost_usd)})")

    # ── Footer ──────────────────────────────────────────────
    lines.append("")
    lines.append(f"  Proof ID: {proof_id}")
    lines.append("")

    return "\n".join(lines)


def save_result(
    arm_a: ArmResult,
    arm_b: ArmResult,
    scenario_name: str,
    proof_id: str,
    output_dir: Optional[Path] = None,
) -> Path:
    """Save the proof result as JSON."""
    if output_dir is None:
        output_dir = Path.home() / ".tokenpak" / "prove" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)

    path = output_dir / f"{proof_id}.json"

    # Strip response_text to keep the file manageable
    result = {
        "proof_id": proof_id,
        "scenario": scenario_name,
        "arm_a": _arm_to_dict(arm_a),
        "arm_b": _arm_to_dict(arm_b),
        "summary": {
            "input_tokens_saved": arm_a.total_input_tokens - arm_b.total_input_tokens,
            "input_tokens_saved_pct": _pct_val(arm_a.total_input_tokens, arm_b.total_input_tokens),
            "cost_saved_usd": round(arm_a.total_cost_usd - arm_b.total_cost_usd, 6),
            "cost_saved_pct": _pct_val(arm_a.total_cost_usd, arm_b.total_cost_usd),
            "cache_read_tokens": arm_b.total_cache_read_tokens,
            "time_saved_s": round(arm_a.total_latency_s - arm_b.total_latency_s, 2),
        },
    }

    path.write_text(json.dumps(result, indent=2))
    return path


def _arm_to_dict(arm: ArmResult) -> dict:
    return {
        "arm_name": arm.arm_name,
        "model": arm.model,
        "provider": arm.provider,
        "total_input_tokens": arm.total_input_tokens,
        "total_output_tokens": arm.total_output_tokens,
        "total_cache_read_tokens": arm.total_cache_read_tokens,
        "total_cost_usd": round(arm.total_cost_usd, 6),
        "total_latency_s": round(arm.total_latency_s, 2),
        "error": arm.error,
        "turns": [
            {
                "turn": t.turn_number,
                "label": t.label,
                "input_tokens": t.input_tokens,
                "output_tokens": t.output_tokens,
                "cache_read_tokens": t.cache_read_tokens,
                "latency_s": round(t.latency_s, 2),
                "cost_usd": round(t.cost_usd, 6),
                "error": t.error,
            }
            for t in arm.turns
        ],
    }


# ── Formatting helpers ──────────────────────────────────────────────────


def _row(label: str, a: str, b: str, delta: str, col_w: int) -> str:
    return f"  {label:24s}{a:>{col_w}s}{b:>{col_w}s}{delta:>{col_w}s}"


def _pct_delta(a: float, b: float) -> str:
    if a == 0:
        return ""
    diff = (b - a) / a * 100
    if abs(diff) < 0.1:
        return "0.0%"
    return f"{diff:+.1f}%"


def _pct(part: float, whole: float) -> str:
    if whole == 0:
        return "0%"
    return f"{part / whole * 100:.1f}%"


def _pct_val(a: float, b: float) -> float:
    if a == 0:
        return 0.0
    return round((a - b) / a * 100, 1)
