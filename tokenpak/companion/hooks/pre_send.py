#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""UserPromptSubmit hook — ultra-lean pre-send pipeline.

Performance critical: this runs on EVERY prompt. Must complete in < 100ms.

Design choices for speed:
    - No tiktoken (char//4 heuristic is within 3% per stress test)
    - No transcript parsing (os.path.getsize is instant)
    - No heavy imports (only stdlib: json, sys, os, sqlite3, pathlib)
    - Journal write is best-effort, non-blocking
    - Budget check uses direct SQLite query, no ORM

Pipeline: read stdin → file-size token estimate → budget check → stderr output

Usage in settings.json::

    {
      "hooks": {
        "UserPromptSubmit": [{
          "type": "command",
          "command": "python3 -m tokenpak.companion.hooks.pre_send"
        }]
      }
    }
"""

from __future__ import annotations

# Minimal imports — stdlib only for speed
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

# Model costs (USD per 1M tokens) — inlined to avoid importing tracker module
_COSTS = {
    "opus": 15.0,
    "sonnet": 3.0,
    "haiku": 0.80,
}
_DEFAULT_INPUT_RATE = 3.0  # sonnet as default


def main() -> int:
    """Hook entry point.  Returns 0 (allow) or 2 (block)."""
    # Parse hook input
    try:
        hook_input = json.loads(sys.stdin.read())
    except Exception:
        return 0  # fail-open: can't parse → allow

    session_id = hook_input.get("session_id", "")
    transcript_path = hook_input.get("transcript_path", "")

    # Check if companion is enabled
    if os.environ.get("TOKENPAK_COMPANION_ENABLED", "1").lower() in ("0", "false", "no"):
        return 0

    # Token estimation: file size / 4 (instant, no parsing)
    tokens_est = 0
    if transcript_path:
        try:
            file_size = os.path.getsize(transcript_path)
            tokens_est = file_size // 4
        except OSError:
            pass

    # Cost estimation
    cost_est = tokens_est * _DEFAULT_INPUT_RATE / 1_000_000

    # Budget check
    budget = float(os.environ.get("TOKENPAK_COMPANION_BUDGET", "0"))
    daily_total = 0.0
    over_budget = False

    if budget > 0:
        daily_total = _get_daily_total()
        if daily_total + cost_est > budget:
            over_budget = True

    # Budget gate — block if over budget
    if over_budget:
        msg = f"tokenpak: budget exceeded (${daily_total:.2f} / ${budget:.2f} daily)"
        print(msg, file=sys.stderr)
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "decision": "block",
                "reason": msg,
            }
        }))
        return 2

    # Journal write (best-effort, non-blocking)
    if session_id and tokens_est > 0:
        _journal_write(session_id, tokens_est, cost_est)

    # Cost estimate to stderr (visible in TUI)
    if tokens_est > 0 and os.environ.get("TOKENPAK_COMPANION_SHOW_COST", "1") != "0":
        parts = [f"tokenpak: ~{tokens_est:,} tokens"]
        parts.append(f"est ${cost_est:.4f}")
        if budget > 0:
            pct = daily_total / budget * 100
            if pct > 50:
                parts.append(f"budget {pct:.0f}%")
        print("  ".join(parts), file=sys.stderr)

    return 0


def _get_daily_total() -> float:
    """Quick SQLite query for today's total cost."""
    import datetime
    db_path = Path(os.environ.get(
        "TOKENPAK_COMPANION_JOURNAL_DIR",
        str(Path.home() / ".tokenpak" / "companion"),
    )) / "budget.db"
    try:
        if not db_path.exists():
            return 0.0
        conn = sqlite3.connect(str(db_path))
        today = datetime.date.today().isoformat()
        row = conn.execute(
            "SELECT COALESCE(SUM(estimated_cost), 0) FROM companion_costs WHERE date = ?",
            (today,),
        ).fetchone()
        conn.close()
        return row[0] if row else 0.0
    except Exception:
        return 0.0


def _journal_write(session_id: str, tokens_est: int, cost_est: float) -> None:
    """Best-effort journal entry — never fails the hook."""
    db_path = Path(os.environ.get(
        "TOKENPAK_COMPANION_JOURNAL_DIR",
        str(Path.home() / ".tokenpak" / "companion"),
    )) / "journal.db"
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                entry_type TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
        """)
        conn.execute(
            "INSERT INTO entries (session_id, timestamp, entry_type, content, metadata_json) VALUES (?, ?, ?, ?, ?)",
            (session_id, time.time(), "auto",
             f"pre-send: ~{tokens_est:,} tokens, est ${cost_est:.4f}",
             json.dumps({"tokens_est": tokens_est, "cost_est": cost_est})),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # never fail the hook


if __name__ == "__main__":
    sys.exit(main())
