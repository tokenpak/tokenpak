# SPDX-License-Identifier: Apache-2.0
"""Format and display the prove comparison report.

Supports N-arm matrix comparisons.  The first arm is used as the
baseline for delta calculations.  Works for 2 arms (legacy A/B)
or any number of arms (matrix mode).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Optional

from .adapter import ArmResult

# ═══════════════════════════════════════════════════════════════════════
# Matrix report (N arms)
# ═══════════════════════════════════════════════════════════════════════


def format_matrix_report(
    arms: list[ArmResult],
    scenario_name: str,
    proof_id: str,
) -> str:
    """Format the comparison report for N arms."""
    if not arms:
        return "  No results to display.\n"

    lines: list[str] = []
    w = 16  # column width for values

    lines.append("")
    lines.append(f'  TokenPak Value Proof — "{scenario_name}"')
    lines.append("  " + "=" * 70)

    n_turns = max((len(a.turns) for a in arms), default=0)
    lines.append(f"  Arms: {len(arms)}  |  Turns: {n_turns}  |  Proof: {proof_id}")
    lines.append("")

    # Column names (arm names, truncated)
    max_name = w - 2
    names = [a.arm_name[:max_name] for a in arms]

    # ── Summary header row ──────────────────────────────────
    label_w = 22
    hdr = f"  {'':>{label_w}s}" + "".join(f"{n:>{w}s}" for n in names)
    if len(arms) > 1:
        hdr += f"{'vs [1]':>{w}s}"
    lines.append(hdr)
    lines.append("  " + "-" * (label_w + w * len(arms) + (w if len(arms) > 1 else 0)))

    # Descriptor row: platform/provider
    desc_row = f"  {'platform/provider':>{label_w}s}"
    for a in arms:
        tag = f"{a.platform}/{a.provider}"
        desc_row += f"{tag:>{w}s}"
    lines.append(desc_row)

    model_row = f"  {'model':>{label_w}s}"
    for a in arms:
        model_row += f"{a.model[:max_name]:>{w}s}"
    lines.append(model_row)
    lines.append("")

    # ── Aggregate metrics ───────────────────────────────────
    baseline = arms[0]

    lines.append(
        _metric_row(
            "Input tokens",
            [a.total_input_tokens for a in arms],
            baseline.total_input_tokens,
            label_w,
            w,
            fmt="int",
        )
    )
    lines.append(
        _metric_row(
            "Cache-read tokens",
            [a.total_cache_read_tokens for a in arms],
            None,
            label_w,
            w,
            fmt="int",
        )
    )
    lines.append(
        _metric_row(
            "Output tokens",
            [a.total_output_tokens for a in arms],
            baseline.total_output_tokens,
            label_w,
            w,
            fmt="int",
        )
    )
    lines.append(
        _metric_row(
            "Total cost",
            [a.total_cost_usd for a in arms],
            baseline.total_cost_usd,
            label_w,
            w,
            fmt="usd",
        )
    )
    lines.append(
        _metric_row(
            "Total time",
            [a.total_latency_s for a in arms],
            baseline.total_latency_s,
            label_w,
            w,
            fmt="time",
        )
    )

    lines.append("  " + "-" * (label_w + w * len(arms) + (w if len(arms) > 1 else 0)))

    # ── Per-turn breakdown ──────────────────────────────────
    lines.append("")
    lines.append("  Per-turn breakdown:")

    for t_idx in range(n_turns):
        turn_data = [a.turns[t_idx] if t_idx < len(a.turns) else None for a in arms]
        label = next((t.label for t in turn_data if t), f"Turn {t_idx + 1}")

        lines.append(f"\n  Turn {t_idx + 1}: {label}")

        inputs = [t.input_tokens if t else 0 for t in turn_data]
        lines.append(
            _metric_row("  Input", inputs, inputs[0] if inputs else 0, label_w, w, fmt="int")
        )

        caches = [t.cache_read_tokens if t else 0 for t in turn_data]
        if any(c > 0 for c in caches):
            lines.append(_metric_row("  Cached", caches, None, label_w, w, fmt="int"))

        outputs = [t.output_tokens if t else 0 for t in turn_data]
        lines.append(
            _metric_row("  Output", outputs, outputs[0] if outputs else 0, label_w, w, fmt="int")
        )

        costs = [t.cost_usd if t else 0 for t in turn_data]
        lines.append(_metric_row("  Cost", costs, costs[0] if costs else 0, label_w, w, fmt="usd"))

        times = [t.latency_s if t else 0 for t in turn_data]
        lines.append(_metric_row("  Time", times, times[0] if times else 0, label_w, w, fmt="time"))

    # ── Feature highlights ──────────────────────────────────
    highlights = []
    for a in arms:
        if a.total_cache_read_tokens:
            highlights.append(f"    {a.arm_name}: {a.total_cache_read_tokens:,} cache-read tokens")
        input_diff = baseline.total_input_tokens - a.total_input_tokens
        if input_diff > 0:
            pct = (
                input_diff / baseline.total_input_tokens * 100 if baseline.total_input_tokens else 0
            )
            highlights.append(
                f"    {a.arm_name}: {input_diff:,} fewer input tokens ({pct:.1f}% compression)"
            )
        cost_diff = baseline.total_cost_usd - a.total_cost_usd
        if cost_diff > 0:
            pct = cost_diff / baseline.total_cost_usd * 100 if baseline.total_cost_usd else 0
            highlights.append(f"    {a.arm_name}: ${cost_diff:.4f} saved ({pct:.1f}% cheaper)")

    if highlights:
        lines.append("")
        lines.append("  Highlights (vs first arm):")
        lines.extend(highlights)

    # ── Footer ──────────────────────────────────────────────
    lines.append("")
    lines.append(f"  Proof ID: {proof_id}")
    lines.append("")

    return "\n".join(lines)


# ── Legacy 2-arm wrapper (backwards compat) ─────────────────────────


def format_report(arm_a: ArmResult, arm_b: ArmResult, scenario_name: str, proof_id: str) -> str:
    return format_matrix_report([arm_a, arm_b], scenario_name, proof_id)


# ═══════════════════════════════════════════════════════════════════════
# Save results
# ═══════════════════════════════════════════════════════════════════════


def save_result(
    arms: list[ArmResult] | ArmResult,
    scenario_name: str,
    proof_id: str,
    output_dir: Optional[Path] = None,
) -> Path:
    """Save proof result as JSON."""
    if isinstance(arms, ArmResult):
        arms = [arms]

    if output_dir is None:
        output_dir = Path.home() / ".tokenpak" / "prove" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)

    path = output_dir / f"{proof_id}.json"

    baseline = arms[0] if arms else None

    result = {
        "proof_id": proof_id,
        "scenario": scenario_name,
        "arms": [_arm_to_dict(a) for a in arms],
        "summary": {
            "arm_count": len(arms),
            "baseline": arms[0].arm_name if arms else "",
            "best_cost": min(a.total_cost_usd for a in arms) if arms else 0,
            "best_cost_arm": min(arms, key=lambda a: a.total_cost_usd).arm_name if arms else "",
            "best_time": min(a.total_latency_s for a in arms) if arms else 0,
            "best_time_arm": min(arms, key=lambda a: a.total_latency_s).arm_name if arms else "",
        },
    }

    path.write_text(json.dumps(result, indent=2))
    return path


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _arm_to_dict(arm: ArmResult) -> dict[str, object]:
    return {
        "arm_name": arm.arm_name,
        "platform": arm.platform,
        "provider": arm.provider,
        "model": arm.model,
        "via_tokenpak": arm.via_tokenpak,
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


def _metric_row(
    label: str,
    values: Sequence[int | float],
    baseline_val: int | float | None,
    label_w: int,
    col_w: int,
    fmt: str = "int",
) -> str:
    """Build one row of the comparison table."""
    row = f"  {label:>{label_w}s}"

    for v in values:
        if fmt == "int":
            row += f"{v:>{col_w},d}" if isinstance(v, int) else f"{int(v):>{col_w},d}"
        elif fmt == "usd":
            row += f"{'$' + f'{v:.4f}':>{col_w}s}"
        elif fmt == "time":
            row += f"{f'{v:.1f}s':>{col_w}s}"
        else:
            row += f"{str(v):>{col_w}s}"

    # Delta column (last arm vs first arm)
    if len(values) > 1 and baseline_val is not None and baseline_val != 0:
        last = values[-1]
        diff = (last - baseline_val) / baseline_val * 100
        if abs(diff) < 0.1:
            row += f"{'0.0%':>{col_w}s}"
        else:
            row += f"{f'{diff:+.1f}%':>{col_w}s}"

    return row
