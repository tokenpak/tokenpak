"""savings command — DEPRECATED. Use `tokenpak status` instead.

`tokenpak savings` is a legacy command. All savings data now appears in the
default `tokenpak status` output (savings-first layout, v3).

This wrapper prints a deprecation notice and then delegates to `tokenpak status`
with equivalent flags, so existing scripts and habits keep working.

Flag mapping:
    tokenpak savings              → tokenpak status
    tokenpak savings --verbose    → tokenpak status --full
    tokenpak savings --json       → tokenpak status --json
    tokenpak savings --period Xd  → (period ignored; status uses live DB)
"""

from __future__ import annotations

import sys
from typing import Optional

_DEPRECATION_NOTICE = """\
⚠️  `tokenpak savings` is deprecated.
    All savings data is now shown in `tokenpak status` (default view).
    Please update your workflow: tokenpak status
"""


def run_savings_cmd(args) -> None:
    """Dispatch handler for 'tokenpak savings' — prints notice then delegates to status."""
    print(_DEPRECATION_NOTICE)

    verbose = getattr(args, "verbose", False)
    as_json = getattr(args, "json", False) or getattr(args, "as_json", False)

    try:
        from tokenpak.cli.commands.status import run, run_full, _run_json

        if as_json:
            _run_json()
        elif verbose:
            run_full()
        else:
            run()
    except ImportError as exc:  # pragma: no cover
        print(f"  (Could not load status command: {exc})", file=sys.stderr)


# ---------------------------------------------------------------------------
# Click entrypoint (if available)
# ---------------------------------------------------------------------------

try:
    import click

    @click.command("savings")
    @click.option("--verbose", "-v", is_flag=True, help="[deprecated] Use tokenpak status --full")
    @click.option("--json", "as_json", is_flag=True, help="[deprecated] Use tokenpak status --json")
    @click.option("--period", default="24h", hidden=True, help="[deprecated] Ignored")
    def savings_cmd(verbose: bool, as_json: bool, period: str) -> None:
        """[DEPRECATED] Use `tokenpak status` instead.

        All savings data is now shown in the default `tokenpak status` output.
        """

        class _Args:
            pass

        a = _Args()
        a.verbose = verbose
        a.json = as_json
        a.as_json = as_json
        a.period = period
        run_savings_cmd(a)

except ImportError:
    savings_cmd = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# _query_savings / _query_by_model — savings analytics (used by tests)
# ---------------------------------------------------------------------------
import sqlite3 as _sqlite3

_MONITOR_DB = ""


def _period_to_days(period: str) -> int:
    """Convert period string like '24h', '7d', '30d' to number of days."""
    period = period.strip()
    if period.endswith("h"):
        hours = int(period[:-1])
        return max(1, (hours + 23) // 24)
    if period.endswith("d"):
        return int(period[:-1])
    return 1


def _query_savings(period: str = "24h", model: str | None = None) -> dict:
    """Return aggregate savings summary from the monitor database."""
    path = _MONITOR_DB
    if not path:
        return {"error": "DB not found", "requests": 0}
    try:
        days = _period_to_days(period)
        con = _sqlite3.connect(path)
        con.row_factory = _sqlite3.Row
        clauses = ["date(timestamp) >= date('now', ?)"]
        params: list = [f"-{days} days"]
        if model:
            clauses.append("model = ?")
            params.append(model)
        where = "WHERE " + " AND ".join(clauses)
        sql = f"""
            SELECT
                COUNT(*) AS requests,
                COALESCE(AVG(input_tokens), 0) AS avg_raw_tokens,
                COALESCE(AVG(CASE WHEN compressed_tokens > 0
                                  THEN compressed_tokens
                                  ELSE input_tokens END), 0) AS avg_compressed_tokens,
                COALESCE(SUM(input_tokens), 0) AS total_raw,
                COALESCE(SUM(CASE WHEN compressed_tokens > 0
                                  THEN compressed_tokens
                                  ELSE input_tokens END), 0) AS total_compressed
            FROM requests {where}
        """
        row = con.execute(sql, params).fetchone()
        con.close()
        if not row:
            return {"requests": 0, "avg_raw_tokens": 0, "avg_compressed_tokens": 0,
                    "tokens_saved_total": 0, "reduction_pct": 0.0}
        total_raw = row["total_raw"] or 0
        total_comp = row["total_compressed"] or 0
        tokens_saved = total_raw - total_comp
        reduction_pct = (tokens_saved / total_raw * 100.0) if total_raw > 0 else 0.0
        return {
            "requests": row["requests"],
            "avg_raw_tokens": int(row["avg_raw_tokens"]),
            "avg_compressed_tokens": int(row["avg_compressed_tokens"]),
            "tokens_saved_total": int(tokens_saved),
            "reduction_pct": reduction_pct,
        }
    except Exception:
        return {"error": "query failed", "requests": 0}


def _query_by_model(period: str = "24h", db_path: str = "") -> list:
    """Return per-model savings rows from the monitor database."""
    path = db_path or _MONITOR_DB
    if not path:
        return []
    try:
        days = _period_to_days(period)
        con = _sqlite3.connect(path)
        con.row_factory = _sqlite3.Row
        sql = """
            SELECT model,
                   COUNT(*) AS requests,
                   AVG(input_tokens) AS avg_raw_tokens,
                   AVG(CASE WHEN compressed_tokens > 0
                             THEN compressed_tokens
                             ELSE input_tokens END) AS avg_compressed_tokens,
                   SUM(input_tokens) - SUM(CASE WHEN compressed_tokens > 0
                                                THEN compressed_tokens
                                                ELSE input_tokens END) AS tokens_saved_total,
                   CASE WHEN SUM(input_tokens) > 0
                        THEN (SUM(input_tokens) - SUM(CASE WHEN compressed_tokens > 0
                                                           THEN compressed_tokens
                                                           ELSE input_tokens END))
                             * 100.0 / SUM(input_tokens)
                        ELSE 0.0 END AS reduction_pct
            FROM requests
            WHERE date(timestamp) >= date('now', ?)
            GROUP BY model
        """
        rows = con.execute(sql, (f"-{days} days",)).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception:
        return []
