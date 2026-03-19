"""optimize command — /tokenpak optimize — Pro+ feature.

Analyzes the current session for cost + token efficiency, suggests better
routing, identifies redundant context, and optionally auto-applies
recommendations.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROXY_BASE = os.environ.get("TOKENPAK_PROXY_URL", "http://127.0.0.1:8766")
_MONITOR_DB = os.environ.get(
    "TOKENPAK_DB",
    os.path.expanduser("~/.openclaw/workspace/.tokenpak/monitor.db"),
)
SEP = "────────────────────────────────────────"

# Model pricing table (per 1M tokens, USD — input/output)
MODEL_COSTS: Dict[str, Dict[str, float]] = {
    "claude-opus-4-5": {"input": 15.00, "output": 75.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-3-5": {"input": 0.25, "output": 1.25},
    "claude-haiku-4-5": {"input": 0.25, "output": 1.25},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gemini-2-flash": {"input": 0.075, "output": 0.30},
    "gemini-pro": {"input": 1.25, "output": 5.00},
    "codex": {"input": 3.00, "output": 12.00},
    "_fallback": {"input": 1.00, "output": 3.00},
}

# Cheaper alternatives: model → [(alt_model, savings_pct_approx, reason)]
MODEL_ALTERNATIVES: Dict[str, List[Tuple[str, int, str]]] = {
    "claude-opus-4-5": [
        ("claude-sonnet-4-5", 80, "similar quality for most tasks"),
        ("claude-haiku-3-5", 95, "best for simple/structured tasks"),
    ],
    "claude-opus-4-6": [
        ("claude-sonnet-4-6", 80, "similar quality for most tasks"),
        ("claude-haiku-4-5", 95, "best for simple/structured tasks"),
    ],
    "claude-sonnet-4-5": [
        ("claude-haiku-3-5", 75, "great for structured/simple tasks"),
        ("gpt-4o-mini", 85, "suitable for lightweight prompts"),
    ],
    "claude-sonnet-4-6": [
        ("claude-haiku-4-5", 75, "great for structured/simple tasks"),
        ("gpt-4o-mini", 85, "suitable for lightweight prompts"),
    ],
    "gpt-4o": [
        ("gpt-4o-mini", 90, "good for simple tasks"),
        ("gemini-2-flash", 97, "fast responses, very low cost"),
    ],
    "gemini-pro": [
        ("gemini-2-flash", 90, "fast responses"),
    ],
    "codex": [
        ("claude-haiku-3-5", 75, "good for structured code tasks"),
    ],
}

# Compression mode thresholds
COMPRESSION_MODES = {
    "none":       (0,  10),
    "light":      (10, 25),
    "balanced":   (25, 45),
    "aggressive": (45, 65),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _proxy_get(path: str, timeout: int = 5) -> Optional[Dict[str, Any]]:
    try:
        with urllib.request.urlopen(f"{PROXY_BASE}{path}", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _db_connect() -> Optional[sqlite3.Connection]:
    db = Path(_MONITOR_DB)
    if not db.exists():
        return None
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def _fmt_cost(c: float) -> str:
    if c < 0.01:
        return f"${c:.4f}"
    return f"${c:.2f}"


def _model_cost_per_request(
    model: str, avg_input: float, avg_output: float
) -> float:
    """Estimate average cost per request given token averages."""
    p = MODEL_COSTS.get(model, MODEL_COSTS["_fallback"])
    return (avg_input * p["input"] + avg_output * p["output"]) / 1_000_000


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------

def _analyze_compression(session: Dict[str, Any]) -> Dict[str, Any]:
    """Derive compression metrics from session stats."""
    tokens_raw = session.get("tokens_raw", 0)
    tokens_saved = session.get("tokens_saved", 0)
    avg_savings = session.get("avg_savings_pct", 0.0)

    current_pct = round(avg_savings, 1)

    # Determine current mode bucket
    current_mode = "unknown"
    for mode, (lo, hi) in COMPRESSION_MODES.items():
        if lo <= current_pct < hi:
            current_mode = mode
            break
    if current_pct >= 65:
        current_mode = "aggressive"

    # Suggest upgrade if room exists
    if current_pct < 45:
        optimal_pct = current_pct + 14
        optimal_mode = "aggressive"
        compression_savings = optimal_pct - current_pct
    else:
        optimal_pct = current_pct
        optimal_mode = current_mode
        compression_savings = 0

    return {
        "current_pct": current_pct,
        "current_mode": current_mode,
        "optimal_pct": round(optimal_pct, 1),
        "optimal_mode": optimal_mode,
        "tokens_raw": tokens_raw,
        "tokens_saved": tokens_saved,
        "additional_savings_pct": round(compression_savings, 1),
    }


def _analyze_model(session: Dict[str, Any]) -> Dict[str, Any]:
    """Compare current model cost to cheaper alternatives."""
    conn = _db_connect()
    current_model = ""
    avg_input = 0.0
    avg_output = 0.0
    cost_per_req = 0.0
    total_cost = session.get("total_cost", 0.0)
    requests = session.get("session_requests", 1) or 1

    if conn:
        today = date.today().isoformat()
        row = conn.execute(
            """
            SELECT model,
                   AVG(input_tokens)  AS avg_in,
                   AVG(output_tokens) AS avg_out,
                   AVG(estimated_cost) AS avg_cost
            FROM requests
            WHERE date(timestamp) = ?
            GROUP BY model
            ORDER BY COUNT(*) DESC
            LIMIT 1
            """,
            [today],
        ).fetchone()
        conn.close()
        if row:
            current_model = row["model"] or ""
            avg_input = float(row["avg_in"] or 0)
            avg_output = float(row["avg_out"] or 0)
            cost_per_req = float(row["avg_cost"] or 0)

    if not current_model:
        # Fall back to proxy health if available
        health = _proxy_get("/health") or {}
        current_model = health.get("model", "claude-sonnet-4-6")
        cost_per_req = total_cost / requests if requests else 0.0

    # Find best cheaper alternative
    alts = MODEL_ALTERNATIVES.get(current_model, [])
    best_alt = None
    best_savings_pct = 0
    best_alt_cost = 0.0

    if alts and avg_input > 0:
        for alt_name, savings_pct, reason in alts:
            alt_cost = _model_cost_per_request(alt_name, avg_input, avg_output)
            if alt_cost < cost_per_req:
                if savings_pct > best_savings_pct:
                    best_savings_pct = savings_pct
                    best_alt = (alt_name, savings_pct, reason)
                    best_alt_cost = alt_cost
    elif alts:
        alt_name, savings_pct, reason = alts[0]
        best_alt = (alt_name, savings_pct, reason)
        best_alt_cost = cost_per_req * (1 - savings_pct / 100)
        best_savings_pct = savings_pct

    return {
        "current_model": current_model,
        "cost_per_request": round(cost_per_req, 4),
        "avg_input_tokens": round(avg_input),
        "avg_output_tokens": round(avg_output),
        "best_alternative": best_alt[0] if best_alt else None,
        "alt_cost_per_request": round(best_alt_cost, 4),
        "alt_savings_pct": best_savings_pct,
        "alt_reason": best_alt[2] if best_alt else None,
    }


def _analyze_redundancy() -> Dict[str, Any]:
    """Detect redundant/duplicate blocks in the DB."""
    conn = _db_connect()
    if not conn:
        return {
            "duplicate_memory_blocks": 0,
            "expired_telemetry_caches": 0,
            "total_redundant_blocks": 0,
            "redundant_tokens": 0,
        }

    # Duplicate memory blocks: same (model, input_tokens, output_tokens) in same day
    dup_row = conn.execute(
        """
        SELECT COUNT(*) AS cnt FROM (
            SELECT model, input_tokens, output_tokens, COUNT(*) AS n
            FROM requests
            WHERE date(timestamp) = date('now')
            GROUP BY model, input_tokens, output_tokens
            HAVING n > 1
        )
        """
    ).fetchone()
    duplicates = int(dup_row["cnt"] or 0)

    # Expired/stale telemetry: requests older than 7 days still in DB
    stale_row = conn.execute(
        """
        SELECT COUNT(*) AS cnt FROM requests
        WHERE date(timestamp) < date('now', '-7 days')
        """
    ).fetchone()
    stale = int(stale_row["cnt"] or 0)
    stale_cache_buckets = min(stale // 50, 5)  # group into cache-bucket estimate

    # Estimate tokens in duplicates
    tok_row = conn.execute(
        """
        SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS tok
        FROM requests
        WHERE date(timestamp) = date('now')
          AND (model, input_tokens, output_tokens) IN (
              SELECT model, input_tokens, output_tokens
              FROM requests
              WHERE date(timestamp) = date('now')
              GROUP BY model, input_tokens, output_tokens
              HAVING COUNT(*) > 1
          )
        """
    ).fetchone()
    redundant_tokens = int(tok_row["tok"] or 0)
    conn.close()

    total = duplicates + stale_cache_buckets
    return {
        "duplicate_memory_blocks": duplicates,
        "expired_telemetry_caches": stale_cache_buckets,
        "total_redundant_blocks": total,
        "redundant_tokens": redundant_tokens,
    }


def _build_recommendations(
    compression: Dict[str, Any],
    model: Dict[str, Any],
    redundancy: Dict[str, Any],
) -> List[Dict[str, Any]]:
    recs = []
    n = 1

    # 1. Compression upgrade
    if compression["additional_savings_pct"] > 0:
        recs.append({
            "n": n,
            "label": f"Switch to {compression['optimal_mode']} compression",
            "detail": (
                f"+{compression['additional_savings_pct']:.0f}% token savings "
                f"(current: {compression['current_pct']:.0f}% → target: {compression['optimal_pct']:.0f}%)"
            ),
            "apply_cmd": f"tokenpak config set compression {compression['optimal_mode']}",
        })
        n += 1

    # 2. Model switch
    if model.get("best_alternative"):
        alt_cost = model["alt_cost_per_request"]
        curr_cost = model["cost_per_request"]
        savings = curr_cost - alt_cost
        recs.append({
            "n": n,
            "label": f"Use {model['best_alternative']} for simple queries",
            "detail": (
                f"-${savings:.3f}/req ({model['alt_savings_pct']}% cheaper) "
                f"— {model['alt_reason']}"
            ),
            "apply_cmd": None,  # routing change, manual
        })
        n += 1

    # 3. Prune redundant blocks
    if redundancy["total_redundant_blocks"] > 0:
        recs.append({
            "n": n,
            "label": f"Prune {redundancy['total_redundant_blocks']} redundant block(s)",
            "detail": (
                f"Free ~{redundancy['redundant_tokens']:,} tokens "
                f"({redundancy['duplicate_memory_blocks']} duplicate memory block(s), "
                f"{redundancy['expired_telemetry_caches']} expired telemetry cache(s))"
            ),
            "apply_cmd": "tokenpak maintenance prune",
        })
        n += 1

    if not recs:
        recs.append({
            "n": 1,
            "label": "No significant optimizations found",
            "detail": "Session is already well-optimized.",
            "apply_cmd": None,
        })

    return recs


# ---------------------------------------------------------------------------
# Output renderers
# ---------------------------------------------------------------------------

def _render_text(
    compression: Dict[str, Any],
    model: Dict[str, Any],
    redundancy: Dict[str, Any],
    recs: List[Dict[str, Any]],
    verbose: bool = False,
) -> None:
    print(f"\nTOKENPAK  |  Optimization Analysis")
    print(SEP)
    print()

    # Compression block
    curr_pct = compression["current_pct"]
    opt_pct = compression["optimal_pct"]
    extra = compression["additional_savings_pct"]
    print(f"  {'Current Compression:':<28}{curr_pct:.0f}%")
    if extra > 0:
        print(f"  {'Optimal Compression:':<28}~{opt_pct:.0f}% (switch to {compression['optimal_mode']})")
    else:
        print(f"  {'Optimal Compression:':<28}{opt_pct:.0f}% (already optimal)")
    print()

    # Model cost block
    curr_cost = model["cost_per_request"]
    print(f"  {'Model Cost:':<28}{_fmt_cost(curr_cost)}/request  [{model['current_model']}]")
    if model.get("best_alternative"):
        alt_cost = model["alt_cost_per_request"]
        savings_pct = model["alt_savings_pct"]
        print(f"  {'Cheaper Alternative:':<28}{model['best_alternative']} ({_fmt_cost(alt_cost)}/request)")
        print(f"  {'Estimated Savings:':<28}▼ {savings_pct}%")
    else:
        print(f"  {'Cheaper Alternative:':<28}None (model already optimal)")
    print()

    # Redundancy block
    dup = redundancy["duplicate_memory_blocks"]
    stale = redundancy["expired_telemetry_caches"]
    total_r = redundancy["total_redundant_blocks"]
    if total_r > 0:
        print(f"  Redundant Context:")
        if dup:
            print(f"    • {dup} duplicate memory block{'s' if dup != 1 else ''}")
        if stale:
            print(f"    • {stale} expired telemetry cache{'s' if stale != 1 else ''}")
        if redundancy["redundant_tokens"]:
            print(f"    • ~{redundancy['redundant_tokens']:,} redundant tokens")
    else:
        print(f"  Redundant Context:          None detected")
    print()

    # Recommendations
    print(f"  Recommendations:")
    for r in recs:
        if r["label"] == "No significant optimizations found":
            print(f"  ✓ {r['detail']}")
        else:
            print(f"  {r['n']}. {r['label']}")
            print(f"     {r['detail']}")
            if r.get("apply_cmd"):
                print(f"     → {r['apply_cmd']}")
    print()

    # Verbose: per-block analysis
    if verbose:
        print(f"  Per-Block Analysis (verbose):")
        print(SEP)
        print(f"  Avg Input Tokens:           {model['avg_input_tokens']:,}")
        print(f"  Avg Output Tokens:          {model['avg_output_tokens']:,}")
        print(f"  Tokens Raw (session):       {compression['tokens_raw']:,}")
        print(f"  Tokens Saved (session):     {compression['tokens_saved']:,}")
        print(f"  Compression Mode:           {compression['current_mode']}")
        print()


def _render_json(
    compression: Dict[str, Any],
    model: Dict[str, Any],
    redundancy: Dict[str, Any],
    recs: List[Dict[str, Any]],
) -> None:
    output = {
        "compression": compression,
        "model": model,
        "redundancy": redundancy,
        "recommendations": recs,
    }
    print(json.dumps(output, indent=2))


def _apply_recommendations(recs: List[Dict[str, Any]]) -> None:
    """Auto-apply any recommendations that have an apply_cmd."""
    import subprocess

    applied = 0
    for r in recs:
        if r.get("apply_cmd"):
            print(f"  → Applying: {r['apply_cmd']}")
            try:
                result = subprocess.run(
                    r["apply_cmd"].split(),
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    print(f"    ✓ Applied: {r['label']}")
                    applied += 1
                else:
                    print(f"    ✖ Failed: {result.stderr.strip() or 'non-zero exit'}")
            except Exception as e:
                print(f"    ✖ Error: {e}")
    if applied == 0:
        print("  No auto-applicable recommendations (manual action needed).")
    print()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_optimize(verbose: bool = False, as_json: bool = False, apply: bool = False) -> None:
    """Run the full optimization analysis."""
    # Pro+ gate
    try:
        from tokenpak.agent.license.activation import is_pro
        if not is_pro():
            print("⚠  /tokenpak optimize requires a Pro (or higher) license.")
            print("   Run: tokenpak activate <key>")
            sys.exit(1)
    except ImportError:
        pass  # license module not available in all installs — proceed

    # Fetch session stats
    session = _proxy_get("/stats/session") or {}

    # Run analysis
    compression = _analyze_compression(session)
    model = _analyze_model(session)
    redundancy = _analyze_redundancy()
    recs = _build_recommendations(compression, model, redundancy)

    # Render
    if as_json:
        _render_json(compression, model, redundancy, recs)
    else:
        _render_text(compression, model, redundancy, recs, verbose=verbose)
        if apply:
            print(f"  Auto-Applying Recommendations:")
            _apply_recommendations(recs)


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

try:
    import click

    @click.command("optimize")
    @click.option("--verbose", "-v", is_flag=True, help="Per-block analysis")
    @click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output")
    @click.option("--apply", is_flag=True, help="Auto-apply recommendations")
    def optimize_cmd(verbose, as_json, apply):
        """Analyze session for cost + token efficiency (Pro+)."""
        run_optimize(verbose=verbose, as_json=as_json, apply=apply)

except ImportError:
    pass
