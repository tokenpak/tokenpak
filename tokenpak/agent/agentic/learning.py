"""Agent Learning Store for TokenPak.

Extracts and persists patterns from Shadow Mode telemetry:
  - Model performance by task type (routing_ledger)
  - Compression mode effectiveness (calibrator)
  - Block utility scores (citation_tracker)
  - Context gap patterns (miss_detector)

Data is persisted to ~/.tokenpak/learning.json.
Feeds model routing decisions, recipe selection, and budget allocation.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_LEARNING_PATH = os.path.expanduser("~/.tokenpak/learning.json")

# Minimum samples before we trust a learned metric
MIN_SAMPLES_THRESHOLD = 5


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_store() -> dict:
    return {
        "version": 1,
        "updated": _now_iso(),
        "model_performance": {},  # {task_type: {model: {acceptance_rate, samples}}}
        "compression_modes": {},  # {risk_class: {mode: {retry_rate, event_count}}}
        "block_utility": {},  # {slice_id: {score, hits, misses, last_cited}}
        "context_gaps": {  # aggregated gap signal counts
            "total": 0,
            "by_signal": {},
            "queries_with_gaps": 0,
            "expansion_triggers": 0,
        },
    }


def _load(path: str) -> dict:
    p = Path(path)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            if isinstance(data, dict) and data.get("version") == 1:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return _empty_store()


def _save(data: dict, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data["updated"] = _now_iso()
    p.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Extraction: routing_ledger → model performance
# ---------------------------------------------------------------------------


def _extract_model_performance(
    ledger_path: str,
    store: dict,
) -> dict:
    """
    Pull model acceptance rates by task_type from the routing_ledger SQLite DB.
    Updates store["model_performance"] in place and returns it.
    """
    import sqlite3

    p = Path(ledger_path)
    if not p.exists():
        return store["model_performance"]

    try:
        conn = sqlite3.connect(str(p), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT
                model_used,
                task_type,
                SUM(CASE WHEN accepted = 1 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN accepted = 0 THEN 1 ELSE 0 END) AS losses,
                COUNT(*) AS total
            FROM transactions
            WHERE accepted IS NOT NULL
            GROUP BY model_used, task_type
        """).fetchall()
        conn.close()
    except sqlite3.Error:
        return store["model_performance"]

    perf: Dict[str, Dict] = {}
    for row in rows:
        task_type = row["task_type"] or "UNKNOWN"
        model = row["model_used"]
        total = row["total"]
        wins = row["wins"]

        if task_type not in perf:
            perf[task_type] = {}

        perf[task_type][model] = {
            "acceptance_rate": round(wins / total, 4) if total > 0 else 0.0,
            "samples": total,
            "wins": wins,
            "losses": row["losses"],
        }

    store["model_performance"] = perf
    return perf


# ---------------------------------------------------------------------------
# Extraction: calibrator → compression mode effectiveness
# ---------------------------------------------------------------------------


def _extract_compression_modes(
    calibration_path: str,
    store: dict,
) -> dict:
    """
    Derive compression mode effectiveness from calibration.json events.
    Updates store["compression_modes"] in place and returns it.
    """
    p = Path(calibration_path)
    if not p.exists():
        return store["compression_modes"]

    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return store["compression_modes"]

    events = data.get("events", [])
    # Build per (risk_class, mode) retry/success counts
    counts: Dict[str, Dict[str, Dict]] = {}  # risk_class → mode → {retries, successes}

    for ev in events:
        mode = ev.get("mode", "unknown").lower()
        ev_type = ev.get("type", "")

        if ev_type == "retry":
            for rc in ev.get("risk_classes", []):
                rc = rc.upper()
                counts.setdefault(rc, {}).setdefault(mode, {"retries": 0, "successes": 0})
                counts[rc][mode]["retries"] += 1
        elif ev_type == "success":
            # Successes don't carry risk_class — attribute to a synthetic "_all" key
            counts.setdefault("_ALL", {}).setdefault(mode, {"retries": 0, "successes": 0})
            counts["_ALL"][mode]["successes"] += 1

    # Compute retry_rate for each (rc, mode)
    result: Dict[str, Dict] = {}
    for rc, modes in counts.items():
        result[rc] = {}
        all_successes = counts.get("_ALL", {})
        for mode, stats in modes.items():
            retries = stats["retries"]
            successes = all_successes.get(mode, {}).get("successes", 0) + stats.get("successes", 0)
            total = retries + successes
            result[rc][mode] = {
                "retry_rate": round(retries / total, 4) if total > 0 else 0.0,
                "retries": retries,
                "successes": successes,
                "event_count": total,
            }

    # Add current overrides for reference
    result["_overrides"] = data.get("overrides", {})
    store["compression_modes"] = result
    return result


# ---------------------------------------------------------------------------
# Extraction: citation_tracker → block utility
# ---------------------------------------------------------------------------


def _extract_block_utility(
    utility_path: str,
    store: dict,
) -> dict:
    """
    Load citation utility scores from utility.json.
    Updates store["block_utility"] in place and returns it.
    """
    p = Path(utility_path)
    if not p.exists():
        return store["block_utility"]

    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return store["block_utility"]

    if not isinstance(data, dict):
        return store["block_utility"]

    store["block_utility"] = data
    return data


# ---------------------------------------------------------------------------
# Extraction: miss_detector → context gap patterns
# ---------------------------------------------------------------------------


def _extract_context_gaps(
    gaps_path: str,
    store: dict,
) -> dict:
    """
    Aggregate context gap signals from gaps.json.
    Updates store["context_gaps"] in place and returns it.
    """
    p = Path(gaps_path)
    if not p.exists():
        return store["context_gaps"]

    try:
        gaps_list = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return store["context_gaps"]

    if not isinstance(gaps_list, list):
        return store["context_gaps"]

    by_signal: Dict[str, int] = {}
    unique_queries: set = set()
    expansion_triggers = 0

    for gap in gaps_list:
        sig = gap.get("signal_type", "UNKNOWN")
        by_signal[sig] = by_signal.get(sig, 0) + 1
        q = gap.get("query", "")
        if q:
            unique_queries.add(q[:100])  # Dedupe by first 100 chars
        # EXPLICIT_ASK or HALLUCINATED_IMPORT = strong expansion signal
        if sig in ("EXPLICIT_ASK", "HALLUCINATED_IMPORT"):
            expansion_triggers += 1

    summary = {
        "total": len(gaps_list),
        "by_signal": by_signal,
        "queries_with_gaps": len(unique_queries),
        "expansion_triggers": expansion_triggers,
    }
    store["context_gaps"] = summary
    return summary


# ---------------------------------------------------------------------------
# Public API: learn()
# ---------------------------------------------------------------------------


def learn(
    ledger_path: Optional[str] = None,
    calibration_path: Optional[str] = None,
    utility_path: Optional[str] = None,
    gaps_path: Optional[str] = None,
    learning_path: str = DEFAULT_LEARNING_PATH,
) -> dict:
    """
    Extract patterns from all telemetry sources and persist to learning.json.

    Paths default to project-relative .tokenpak/ paths if not specified.

    Args:
        ledger_path:       Path to routing_ledger.db (SQLite)
        calibration_path:  Path to calibration.json
        utility_path:      Path to utility.json (citation scores)
        gaps_path:         Path to gaps.json (miss detector)
        learning_path:     Path to write learning.json

    Returns:
        Updated learning store dict.
    """
    _ledger = ledger_path or ".tokenpak/routing_ledger.db"
    _calib = calibration_path or ".tokenpak/calibration.json"
    _utility = utility_path or ".tokenpak/utility.json"
    _gaps = gaps_path or ".tokenpak/gaps.json"

    store = _load(learning_path)

    _extract_model_performance(_ledger, store)
    _extract_compression_modes(_calib, store)
    _extract_block_utility(_utility, store)
    _extract_context_gaps(_gaps, store)

    _save(store, learning_path)
    return store


# ---------------------------------------------------------------------------
# Public API: get_best_model()
# ---------------------------------------------------------------------------


def get_best_model(
    task_type: str,
    learning_path: str = DEFAULT_LEARNING_PATH,
    min_samples: int = MIN_SAMPLES_THRESHOLD,
) -> Optional[str]:
    """
    Return the model with the highest acceptance rate for a given task_type.

    Returns None if no learned data exists or no model meets min_samples.

    Args:
        task_type:     Task type string (e.g. "CODING", "QA").
        learning_path: Path to learning.json.
        min_samples:   Minimum samples required before trusting the metric.

    Returns:
        Model name string or None.
    """
    store = _load(learning_path)
    task_data = store.get("model_performance", {}).get(task_type.upper(), {})
    if not task_data:
        return None

    best_model: Optional[str] = None
    best_rate = -1.0

    for model, stats in task_data.items():
        if stats.get("samples", 0) < min_samples:
            continue
        rate = stats.get("acceptance_rate", 0.0)
        if rate > best_rate:
            best_rate = rate
            best_model = model

    return best_model


# ---------------------------------------------------------------------------
# Public API: get_effective_compression()
# ---------------------------------------------------------------------------


def get_effective_compression(
    risk_class: str,
    base_mode: str = "hybrid",
    learning_path: str = DEFAULT_LEARNING_PATH,
) -> str:
    """
    Suggest compression mode for a risk class based on learned retry rates.

    If learned retry rate for (risk_class, base_mode) > 20%, suggest
    one step stricter. Never downgrades below "strict".

    Args:
        risk_class:    Risk class string (e.g. "CODE", "NARRATIVE").
        base_mode:     Requested base compression mode.
        learning_path: Path to learning.json.

    Returns:
        Effective compression mode string.
    """
    _MODE_ORDER = ["aggressive", "hybrid", "strict"]
    store = _load(learning_path)
    rc_data = store.get("compression_modes", {}).get(risk_class.upper(), {})
    mode_data = rc_data.get(base_mode.lower(), {})

    retry_rate = mode_data.get("retry_rate", 0.0)
    event_count = mode_data.get("event_count", 0)

    if event_count < MIN_SAMPLES_THRESHOLD or retry_rate <= 0.20:
        return base_mode.lower()

    # Step up one level toward strict
    try:
        idx = _MODE_ORDER.index(base_mode.lower())
        return _MODE_ORDER[min(idx + 1, len(_MODE_ORDER) - 1)]
    except ValueError:
        return base_mode.lower()


# ---------------------------------------------------------------------------
# Public API: load() / reset()
# ---------------------------------------------------------------------------


def load(learning_path: str = DEFAULT_LEARNING_PATH) -> dict:
    """Load and return the current learning store."""
    return _load(learning_path)


def reset(learning_path: str = DEFAULT_LEARNING_PATH) -> None:
    """Clear all learned data and reset to empty store."""
    store = _empty_store()
    _save(store, learning_path)


# ---------------------------------------------------------------------------
# CLI helpers (for `tokenpak learn status` / `tokenpak learn reset`)
# ---------------------------------------------------------------------------


def cmd_learn_status(learning_path: str = DEFAULT_LEARNING_PATH) -> None:
    """Print a human-readable summary of learned patterns."""
    store = _load(learning_path)

    SEP = "────────────────────────────────────────"
    print("TOKENPAK  |  Learned Patterns")
    print(SEP)
    print(f"{'Updated':<28}{store.get('updated', 'n/a')}")
    print()

    # Model performance
    mp = store.get("model_performance", {})
    if mp:
        print("📊  Model Performance by Task Type")
        for task_type, models in sorted(mp.items()):
            print(f"  {task_type}")
            for model, stats in sorted(
                models.items(),
                key=lambda kv: kv[1].get("acceptance_rate", 0),
                reverse=True,
            ):
                rate = stats.get("acceptance_rate", 0)
                samples = stats.get("samples", 0)
                bar = "█" * int(rate * 10)
                flag = " ✓" if samples >= MIN_SAMPLES_THRESHOLD else " (low data)"
                print(f"    {model:<40} {rate*100:5.1f}%  [{bar:<10}]  n={samples}{flag}")
        print()
    else:
        print("📊  Model Performance  — no data yet\n")

    # Compression modes
    cm = store.get("compression_modes", {})
    overrides = cm.pop("_overrides", {})
    if cm:
        print("🗜   Compression Mode Effectiveness")
        for rc, modes in sorted(cm.items()):
            if rc.startswith("_"):
                continue
            print(f"  {rc}")
            for mode, stats in sorted(modes.items()):
                retry_rate = stats.get("retry_rate", 0)
                events = stats.get("event_count", 0)
                flag = " ⚠️  (high retry)" if retry_rate > 0.20 else ""
                print(f"    {mode:<14} retry={retry_rate*100:.1f}%  n={events}{flag}")
        if overrides:
            print(f"  Active overrides: {overrides}")
        print()
    else:
        print("🗜   Compression Modes  — no data yet\n")

    # Block utility
    bu = store.get("block_utility", {})
    if bu:
        # Show top 10 and bottom 5 by score
        scored = [(sid, v.get("score", 5.0)) for sid, v in bu.items()]
        scored.sort(key=lambda x: x[1], reverse=True)
        print(f"📌  Block Utility  ({len(bu)} blocks tracked)")
        print("  Top cited:")
        for sid, score in scored[:5]:
            print(f"    {sid[:50]:<52} score={score:.1f}")
        if len(scored) > 5:
            print("  Least useful:")
            for sid, score in scored[-3:]:
                print(f"    {sid[:50]:<52} score={score:.1f}")
        print()
    else:
        print("📌  Block Utility  — no data yet\n")

    # Context gaps
    cg = store.get("context_gaps", {})
    total = cg.get("total", 0)
    if total > 0:
        print(f"🔍  Context Gaps  ({total} total)")
        for sig, count in sorted(cg.get("by_signal", {}).items()):
            print(f"    {sig:<30} {count}")
        print(f"  Unique queries with gaps:  {cg.get('queries_with_gaps', 0)}")
        print(f"  Expansion triggers:        {cg.get('expansion_triggers', 0)}")
    else:
        print("🔍  Context Gaps  — no data yet")
    print()
