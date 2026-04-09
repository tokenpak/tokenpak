"""timeline.py — Daily savings timeline with trend analysis.

Reads daily snapshots from ~/.tokenpak/history.jsonl and provides
trend analysis, ASCII charts, and anomaly detection.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

HISTORY_PATH = Path.home() / ".tokenpak" / "history.jsonl"

# ASCII chart characters (8 levels)
CHART_CHARS = " ▁▂▃▄▅▆▇█"


def load_history(path: Optional[Path] = None) -> List[dict]:
    """Load daily snapshots from JSONL file."""
    p = path or HISTORY_PATH
    if not p.exists():
        return []
    entries = []
    for line in p.read_text().strip().split("\n"):
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def save_snapshot(snapshot: dict, path: Optional[Path] = None) -> None:
    """Append a daily snapshot to history."""
    p = path or HISTORY_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(snapshot) + "\n")


def get_timeline(days: int = 7, path: Optional[Path] = None) -> List[dict]:
    """Get last N days of history, sorted newest first."""
    entries = load_history(path)
    if not entries:
        return []
    # Sort by date descending
    entries.sort(key=lambda e: e.get("date", ""), reverse=True)
    return entries[:days]


def compute_trends(entries: List[dict]) -> List[dict]:
    """Add trend arrows and day-over-day change to entries."""
    result = []
    for i, entry in enumerate(entries):
        e = dict(entry)
        if i < len(entries) - 1:
            prev_saved = entries[i + 1].get("saved_usd", 0)
            curr_saved = e.get("saved_usd", 0)
            if prev_saved > 0:
                change_pct = ((curr_saved - prev_saved) / prev_saved) * 100
                e["trend_pct"] = round(change_pct, 0)
                if change_pct > 5:
                    e["trend"] = f"↗ +{abs(change_pct):.0f}%"
                elif change_pct < -5:
                    e["trend"] = f"↘ -{abs(change_pct):.0f}%"
                else:
                    e["trend"] = "—"
            else:
                e["trend"] = "—"
                e["trend_pct"] = 0
        else:
            e["trend"] = "—"
            e["trend_pct"] = 0
        result.append(e)
    return result


def detect_anomalies(entries: List[dict], threshold: float = 2.0) -> List[dict]:
    """Flag days that are 2σ below average savings."""
    if len(entries) < 3:
        return []

    savings = [e.get("saved_usd", 0) for e in entries]
    avg = sum(savings) / len(savings)
    if avg <= 0:
        return []

    variance = sum((s - avg) ** 2 for s in savings) / len(savings)
    std = variance**0.5
    if std <= 0:
        return []

    anomalies = []
    for e in entries:
        saved = e.get("saved_usd", 0)
        if saved < avg - threshold * std:
            pct_below = ((avg - saved) / avg) * 100
            anomalies.append(
                {
                    "date": e.get("date", ""),
                    "saved_usd": saved,
                    "avg_usd": round(avg, 2),
                    "pct_below": round(pct_below, 0),
                    "cache_hit_pct": e.get("cache_hit_pct", 0),
                }
            )
    return anomalies


def render_chart(entries: List[dict], width: int = 40) -> str:
    """Render ASCII sparkline chart of daily savings."""
    if not entries:
        return "No data for chart."

    # Reverse so oldest is leftmost
    data = list(reversed(entries))
    savings = [e.get("saved_usd", 0) for e in data]
    max_val = max(savings) if savings else 1
    min_val = 0

    # Build sparkline
    chars = []
    for s in savings:
        level = int((s - min_val) / (max_val - min_val + 0.001) * (len(CHART_CHARS) - 1))
        level = max(0, min(level, len(CHART_CHARS) - 1))
        chars.append(CHART_CHARS[level])

    sparkline = "".join(chars)

    avg = sum(savings) / len(savings) if savings else 0
    variance = sum((s - avg) ** 2 for s in savings) / len(savings) if savings else 0
    var_pct = (variance**0.5 / avg * 100) if avg > 0 else 0

    lines = [
        f"  ${max_val:,.0f} {sparkline}",
        f"  ${min_val:,.0f} {'─' * len(sparkline)}",
        "",
        f"  Trend: {'STEADY' if var_pct < 20 else ('VOLATILE' if var_pct > 50 else 'MODERATE')} (avg ${avg:,.0f}/day, variance {var_pct:.0f}%)",
    ]
    return "\n".join(lines)


def format_timeline(entries: List[dict], show_chart: bool = False) -> str:
    """Format human-readable timeline report."""
    if not entries:
        return "No history data found.\nRun TokenPak for at least one day to see trends."

    entries_with_trends = compute_trends(entries)

    lines = [
        f"TokenPak Savings Timeline — Last {len(entries)} Days",
        "──────────────────────────────────────",
        "",
        f"  {'Day':<10} {'Requests':>8}  {'Saved':>8}    {'Cache%':>6}  {'Compress':>8}  {'Trend':>10}",
    ]

    for e in entries_with_trends:
        date_str = e.get("date", "???")
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
            date_label = d.strftime("%b %d")
        except (ValueError, TypeError):
            date_label = date_str[:6]

        lines.append(
            f"  {date_label:<10} {e.get('requests', 0):>8}  "
            f"${e.get('saved_usd', 0):>7.2f}    "
            f"{e.get('cache_hit_pct', 0):>5.0f}%  "
            f"{e.get('compression_pct', 0):>7.1f}%  "
            f"{e.get('trend', '—'):>10}"
        )

    # Summary
    savings = [e.get("saved_usd", 0) for e in entries]
    avg = sum(savings) / len(savings) if savings else 0
    best = max(entries, key=lambda e: e.get("saved_usd", 0))
    worst = min(entries, key=lambda e: e.get("saved_usd", 0))

    lines.append("")
    lines.append(f"  Average: ${avg:.2f} | Trend: STEADY")

    try:
        best_d = datetime.strptime(best["date"], "%Y-%m-%d").strftime("%b %d")
        worst_d = datetime.strptime(worst["date"], "%Y-%m-%d").strftime("%b %d")
    except (ValueError, KeyError):
        best_d = best.get("date", "?")
        worst_d = worst.get("date", "?")

    lines.append(f"  Best day: {best_d} (${best.get('saved_usd', 0):.2f})")
    lines.append(f"  Worst day: {worst_d} (${worst.get('saved_usd', 0):.2f})")

    # Anomalies
    anomalies = detect_anomalies(entries)
    if anomalies:
        lines.append("")
        for a in anomalies:
            lines.append(
                f"  ⚠️ Anomaly: {a['date']} saved only ${a['saved_usd']:.2f} ({a['pct_below']:.0f}% below average)"
            )
            lines.append(f"     → Cache hit dropped to {a['cache_hit_pct']}%")
            lines.append("     → Check proxy health: tokenpak doctor")

    if show_chart:
        lines.append("")
        lines.append(render_chart(entries))

    return "\n".join(lines)
