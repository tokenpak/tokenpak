"""Agent Learning Store for TokenPak.

Extracts and persists patterns from Shadow Mode telemetry:
  - Model performance by task type (routing_ledger)
  - Compression mode effectiveness (calibrator)
  - Block utility scores (citation_tracker)
  - Context gap patterns (miss_detector)
  - Quality-per-token metrics: outcome_score / tokens_used per model/mode/task

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
        # quality_per_token: outcome_score / tokens_used
        # {(model, compression_mode, task_type): {avg_qpt, samples, total_outcome, total_tokens}}
        "quality_per_token": {},  # key = "<model>|<compression_mode>|<task_type>"
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
# Extraction: routing_ledger → quality_per_token
# ---------------------------------------------------------------------------


def _extract_quality_per_token(
    ledger_path: str,
    store: dict,
    compression_mode: str = "unknown",
) -> dict:
    """
    Compute quality_per_token from routing_ledger transactions.

    quality_per_token = outcome_score / tokens_used
    - outcome_score: accepted=1 → 1.0; accepted=0 → 0.0 (no partial in DB)
    - tokens_used: context_tokens + response_tokens

    Aggregated per key "<model>|<compression_mode>|<task_type>".

    Args:
        ledger_path:       Path to routing_ledger.db (SQLite).
        store:             Current learning store dict (mutated in place).
        compression_mode:  Compression mode label to attribute ledger rows to.

    Returns:
        Updated store["quality_per_token"] dict.
    """
    import sqlite3

    p = Path(ledger_path)
    if not p.exists():
        return store.get("quality_per_token", {})

    try:
        conn = sqlite3.connect(str(p), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT
                model_used,
                task_type,
                accepted,
                context_tokens,
                response_tokens
            FROM transactions
            WHERE accepted IS NOT NULL
              AND (context_tokens + response_tokens) > 0
        """).fetchall()
        conn.close()
    except sqlite3.Error:
        return store.get("quality_per_token", {})

    # Aggregate per (model, compression_mode, task_type)
    agg: Dict[str, Dict] = {}
    for row in rows:
        task_type = (row["task_type"] or "UNKNOWN").upper()
        model = row["model_used"] or "unknown"
        tokens_used = (row["context_tokens"] or 0) + (row["response_tokens"] or 0)
        outcome_score = 1.0 if row["accepted"] == 1 else 0.0
        qpt = outcome_score / tokens_used

        key = f"{model}|{compression_mode}|{task_type}"
        if key not in agg:
            agg[key] = {
                "model": model,
                "compression_mode": compression_mode,
                "task_type": task_type,
                "total_outcome": 0.0,
                "total_tokens": 0,
                "samples": 0,
                "avg_qpt": 0.0,
            }
        agg[key]["total_outcome"] += outcome_score
        agg[key]["total_tokens"] += tokens_used
        agg[key]["samples"] += 1

    # Compute avg_qpt for each key
    for key, stats in agg.items():
        if stats["total_tokens"] > 0:
            stats["avg_qpt"] = round(stats["total_outcome"] / stats["total_tokens"], 8)

    store["quality_per_token"] = agg
    return agg


# ---------------------------------------------------------------------------
# Public API: record_quality_per_token()
# ---------------------------------------------------------------------------


def record_quality_per_token(
    model: str,
    task_type: str,
    outcome_score: float,
    tokens_used: int,
    compression_mode: str = "unknown",
    learning_path: str = DEFAULT_LEARNING_PATH,
) -> None:
    """
    Record a single quality_per_token observation into the learning store.

    Incrementally updates the running average without requiring a full ledger
    re-scan. Use for real-time recording from the proxy or agent.

    Args:
        model:            Model name (e.g. "claude-sonnet-4-6").
        task_type:        Task type string (e.g. "CODING", "QA").
        outcome_score:    1.0 = success, 0.5 = partial, 0.0 = failure.
        tokens_used:      Total tokens consumed (context + response).
        compression_mode: Active compression mode (e.g. "aggressive", "hybrid").
        learning_path:    Path to learning.json.
    """
    if tokens_used <= 0:
        return

    store = _load(learning_path)
    qpt_map: Dict[str, Dict] = store.setdefault("quality_per_token", {})

    key = f"{model}|{compression_mode}|{task_type.upper()}"
    if key not in qpt_map:
        qpt_map[key] = {
            "model": model,
            "compression_mode": compression_mode,
            "task_type": task_type.upper(),
            "total_outcome": 0.0,
            "total_tokens": 0,
            "samples": 0,
            "avg_qpt": 0.0,
        }

    entry = qpt_map[key]
    entry["total_outcome"] += outcome_score
    entry["total_tokens"] += tokens_used
    entry["samples"] += 1
    entry["avg_qpt"] = round(entry["total_outcome"] / entry["total_tokens"], 8)

    _save(store, learning_path)


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
    _extract_quality_per_token(_ledger, store)

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
# Public API: get_best_quality_per_token()
# ---------------------------------------------------------------------------


def get_best_quality_per_token(
    task_type: str,
    learning_path: str = DEFAULT_LEARNING_PATH,
    min_samples: int = MIN_SAMPLES_THRESHOLD,
) -> Optional[Dict]:
    """
    Return the (model, compression_mode) combo with the highest avg quality_per_token
    for a given task_type.

    Returns None if no data exists or no entry meets min_samples.

    Args:
        task_type:     Task type string (e.g. "CODING", "QA").
        learning_path: Path to learning.json.
        min_samples:   Minimum observations required before trusting the metric.

    Returns:
        Dict with keys: model, compression_mode, task_type, avg_qpt, samples
        or None.
    """
    store = _load(learning_path)
    qpt_map = store.get("quality_per_token", {})
    task_upper = task_type.upper()

    best: Optional[Dict] = None
    best_qpt = -1.0

    for key, stats in qpt_map.items():
        if stats.get("task_type", "").upper() != task_upper:
            continue
        if stats.get("samples", 0) < min_samples:
            continue
        avg = stats.get("avg_qpt", 0.0)
        if avg > best_qpt:
            best_qpt = avg
            best = {
                "model": stats["model"],
                "compression_mode": stats["compression_mode"],
                "task_type": stats["task_type"],
                "avg_qpt": avg,
                "samples": stats["samples"],
            }

    return best


# ---------------------------------------------------------------------------
# Public API: get_compression_quality_signal()
# ---------------------------------------------------------------------------


def get_compression_quality_signal(
    model: str,
    task_type: str,
    learning_path: str = DEFAULT_LEARNING_PATH,
    min_samples: int = MIN_SAMPLES_THRESHOLD,
) -> Dict:
    """
    Compare quality_per_token across compression modes for a model+task_type.

    Used by cost_router to decide whether compression improves or degrades QPT.

    Returns a dict with:
      - best_mode: compression mode with highest avg_qpt (or None)
      - prefer_compression: True if aggressive/hybrid beats strict on QPT
      - modes: {mode: {avg_qpt, samples}} for each tracked mode
      - recommendation: human-readable string

    Args:
        model:         Model name.
        task_type:     Task type string.
        learning_path: Path to learning.json.
        min_samples:   Minimum samples threshold.

    Returns:
        Signal dict.
    """
    store = _load(learning_path)
    qpt_map = store.get("quality_per_token", {})
    task_upper = task_type.upper()
    model_lower = model.lower()

    modes: Dict[str, Dict] = {}
    for key, stats in qpt_map.items():
        if stats.get("task_type", "").upper() != task_upper:
            continue
        if stats.get("model", "").lower() != model_lower:
            continue
        mode = stats.get("compression_mode", "unknown")
        modes[mode] = {
            "avg_qpt": stats.get("avg_qpt", 0.0),
            "samples": stats.get("samples", 0),
        }

    # Filter to modes with enough samples
    trusted = {m: v for m, v in modes.items() if v["samples"] >= min_samples}

    if not trusted:
        return {
            "best_mode": None,
            "prefer_compression": False,
            "modes": modes,
            "recommendation": "insufficient data",
        }

    best_mode = max(trusted, key=lambda m: trusted[m]["avg_qpt"])
    strict_qpt = trusted.get("strict", {}).get("avg_qpt", 0.0)
    best_qpt = trusted[best_mode]["avg_qpt"]

    # Prefer compression if best non-strict mode beats strict
    prefer_compression = best_mode != "strict" and best_qpt >= strict_qpt

    if prefer_compression:
        recommendation = (
            f"Use {best_mode} compression — best QPT "
            f"({best_qpt:.2e}) vs strict ({strict_qpt:.2e})"
        )
    else:
        recommendation = (
            f"Back off compression — strict gives best QPT ({strict_qpt:.2e})"
            if "strict" in trusted
            else f"Use {best_mode} mode (only trusted option)"
        )

    return {
        "best_mode": best_mode,
        "prefer_compression": prefer_compression,
        "modes": modes,
        "recommendation": recommendation,
    }


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

    # Quality-per-token
    qpt_map = store.get("quality_per_token", {})
    if qpt_map:
        print(f"⚡  Quality-per-Token  ({len(qpt_map)} combos tracked)")
        # Group by task_type
        by_task: Dict[str, list] = {}
        for key, stats in qpt_map.items():
            tt = stats.get("task_type", "UNKNOWN")
            by_task.setdefault(tt, []).append(stats)
        for tt in sorted(by_task):
            entries = sorted(by_task[tt], key=lambda s: s.get("avg_qpt", 0.0), reverse=True)
            print(f"  {tt}")
            for stats in entries:
                model = stats.get("model", "?")
                mode = stats.get("compression_mode", "?")
                avg = stats.get("avg_qpt", 0.0)
                n = stats.get("samples", 0)
                flag = " ✓" if n >= MIN_SAMPLES_THRESHOLD else " (low data)"
                print(f"    {model:<36} [{mode:<12}] QPT={avg:.2e}  n={n}{flag}")
        print()
    else:
        print("⚡  Quality-per-Token  — no data yet")
    print()


# ---------------------------------------------------------------------------
# Integration: memory_promoter bridge
# ---------------------------------------------------------------------------


def record_lesson(
    lesson_id: str,
    content: str,
    outcome: Optional[float] = None,
    specificity_score: float = 0.5,
    material_savings: float = 0.0,
    metadata: Optional[dict] = None,
    memory_path: Optional[str] = None,
) -> "object":
    """Record a lesson observation via the memory promotion system.

    Delegates to memory_promoter.record_lesson(). New lessons start at Tier 1
    and may be promoted via run_memory_promotion() once sufficient evidence
    is accumulated.

    Args:
        lesson_id:         Unique lesson key (e.g. "model_routing_CODING").
        content:           Human-readable description of what was learned.
        outcome:           1.0 = success, 0.0 = failure, None = unknown.
        specificity_score: How actionable the lesson is (0.0–1.0).
        material_savings:  Estimated reduction in future work (0.0–1.0).
        metadata:          Optional extra context.
        memory_path:       Override path to memory_tiers.json.

    Returns:
        Lesson dataclass from memory_promoter.
    """
    from tokenpak.agent.agentic.memory_promoter import (
        DEFAULT_MEMORY_PATH,
        record_lesson as _record,
    )

    return _record(
        lesson_id=lesson_id,
        content=content,
        outcome=outcome,
        specificity_score=specificity_score,
        material_savings=material_savings,
        metadata=metadata,
        memory_path=memory_path or DEFAULT_MEMORY_PATH,
    )


def run_memory_promotion(
    memory_path: Optional[str] = None,
) -> Dict[str, str]:
    """Run a full promotion + demotion sweep over all tracked lessons.

    Promotes lessons that pass tier gates, demotes expired/unused ones,
    and removes Tier-1 lessons that have exceeded their TTL.

    Args:
        memory_path: Override path to memory_tiers.json.

    Returns:
        Dict of {lesson_id: action_taken} for all modified lessons.
    """
    from tokenpak.agent.agentic.memory_promoter import (
        DEFAULT_MEMORY_PATH,
        promote_all,
    )

    return promote_all(memory_path=memory_path or DEFAULT_MEMORY_PATH)


def get_durable_lessons(
    memory_path: Optional[str] = None,
) -> list:
    """Return all Tier 4 (Durable / permanent) lessons.

    Args:
        memory_path: Override path to memory_tiers.json.

    Returns:
        List of Lesson dataclasses.
    """
    from tokenpak.agent.agentic.memory_promoter import (
        DEFAULT_MEMORY_PATH,
        get_durable_lessons as _get_durable,
    )

    return _get_durable(memory_path=memory_path or DEFAULT_MEMORY_PATH)
