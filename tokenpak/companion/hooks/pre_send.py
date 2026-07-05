#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""UserPromptSubmit hook — ultra-lean pre-send pipeline.

Performance critical: this runs on EVERY prompt. Must complete in < 100ms.

Design choices for speed:
    - No tiktoken (char//4 heuristic is within 3% per stress test)
    - No transcript parsing (os.path.getsize is instant)
    - No heavy imports (stdlib + companion._sqlite, which is itself
      stdlib-only shared SQLite plumbing; the parent packages are already
      imported by the ``-m`` invocation)
    - Journal write is best-effort, non-blocking; dropped writes are
      counted in run/dropped-writes.log instead of vanishing silently
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

# Minimal imports — stdlib only for speed (companion._sqlite is stdlib-only
# shared plumbing: connection pragmas + the canonical journal/budget DDL)
import json
import os
import sys
import time
from pathlib import Path

from tokenpak.companion import _sqlite as _db

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
    prompt_text = hook_input.get("prompt", "") or ""

    # Check if companion is enabled
    if os.environ.get("TOKENPAK_COMPANION_ENABLED", "1").lower() in ("0", "false", "no"):
        return 0

    # Session-binding keystone: persist the live session id so the
    # (separate-process) companion MCP server can bind to it. The MCP server
    # never sees the hook payload, so this run-dir marker is the only bridge.
    # Only the real session id is bound — the anon-{pid} journal fallback below
    # is NOT a handoff identity. Best-effort; never fails the hook.
    if session_id:
        _write_session_marker(session_id)

    # Token estimation: transcript size + prompt text, both // 4 (instant).
    # Cron/one-shot `--print` invocations have no transcript on the first hook
    # fire — fall back to the prompt text so we still journal the cycle.
    tokens_est = 0
    if transcript_path:
        try:
            tokens_est += os.path.getsize(transcript_path) // 4
        except OSError:
            pass
    if prompt_text:
        tokens_est += len(prompt_text) // 4

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
        # Blocking the request means the full estimated tokens never went to
        # the provider — record that as a real prompt-side saving so
        # `tokenpak status` Prompt-side plane reports it honestly.
        try:
            _journal_savings(
                session_id or f"budget-block-{os.getpid()}-{int(time.time())}",
                tool="budget_gate",
                tokens_avoided=tokens_est,
                cost_avoided_usd=cost_est,
            )
        except Exception:
            pass  # never fail the block decision on journal error
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

    # Journal write (best-effort, non-blocking). Log even when tokens_est is 0
    # so we still record that a cycle fired — useful for detecting silent
    # failures. Fabricate a session_id from PID+time if hook_input lacks one
    # (e.g. some older Claude CLI builds omit it in --print mode).
    if not session_id:
        session_id = f"anon-{os.getpid()}-{int(time.time())}"
    _journal_write(session_id, tokens_est, cost_est)
    # Record the pre-send cost estimate into companion_costs so per-session
    # daily spend is actually tracked (this table is the basis for the budget
    # gate above and for `tokenpak status` companion cost). Historically these
    # rows landed with session_id='' because no live writer was wired after a
    # refactor; this carries the real session_id. Best-effort, never fails the
    # hook. Recorded AFTER the gate read above, so no double-count this cycle.
    _record_cost(session_id, tokens_est, cost_est)

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


def _write_session_marker(session_id: str) -> None:
    """Persist the live session id to the run-dir marker so the companion MCP
    server (a separate process) can bind ``state.session_id`` to it. Atomic
    write via tmp+replace. Best-effort; never fails the hook."""
    try:
        run_dir = Path(os.environ.get(
            "TOKENPAK_COMPANION_JOURNAL_DIR",
            str(Path.home() / ".tokenpak" / "companion"),
        )) / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        # pid-unique temp name so two concurrent hook processes can't
        # interleave writes to the same temp file before the atomic rename.
        tmp = run_dir / f"current-session.{os.getpid()}.tmp"
        tmp.write_text(session_id.strip(), encoding="utf-8")
        tmp.replace(run_dir / "current-session")
    except Exception:
        pass  # never fail the hook


def _get_daily_total() -> float:
    """Quick SQLite query for today's truthful spend.

    Per (session, day): sums actual rows when present, otherwise takes the
    latest estimate (companion._sqlite.DAILY_SPEND_SQL) — the gate reads
    true marginal spend, never estimate + actual for the same traffic and
    never a summed series of cumulative transcript estimates.
    """
    import datetime
    db_path = Path(os.environ.get(
        "TOKENPAK_COMPANION_JOURNAL_DIR",
        str(Path.home() / ".tokenpak" / "companion"),
    )) / "budget.db"
    try:
        if not db_path.exists():
            return 0.0
        conn = _db.connect(db_path)
        # Additive migration so the kind-aware query works on databases
        # created before the 'kind' column existed.
        _db.ensure_costs_schema(conn)
        today = datetime.date.today().isoformat()
        row = conn.execute(_db.DAILY_SPEND_SQL, (today,)).fetchone()
        conn.close()
        return float(row[0] or 0.0) if row else 0.0
    except Exception:
        return 0.0


def _journal_savings(
    session_id: str, tool: str, tokens_avoided: int, cost_avoided_usd: float
) -> None:
    """Record a prompt-side savings entry matching the status attribution contract.

    Writes entry_type='companion_savings' with metadata {tool, tokens_avoided,
    cost_avoided_usd} so ``tokenpak status`` Prompt-side plane reports it.
    Uses the canonical journal schema from companion._sqlite (shared with
    JournalStore) — the hook must never carry a divergent DDL copy.
    """
    db_path = Path(os.environ.get(
        "TOKENPAK_COMPANION_JOURNAL_DIR",
        str(Path.home() / ".tokenpak" / "companion"),
    )) / "journal.db"
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = _db.connect(db_path)
        _db.ensure_journal_schema(conn)
        meta = {
            "tool": tool,
            "tokens_avoided": int(max(0, tokens_avoided)),
            "cost_avoided_usd": float(max(0.0, cost_avoided_usd)),
        }
        content = (
            f"{tool}: -{meta['tokens_avoided']:,} tokens "
            f"(~${meta['cost_avoided_usd']:.4f})"
        )
        metadata_json = json.dumps(meta)
        conn.execute(
            "INSERT OR IGNORE INTO entries "
            "(session_id, timestamp, entry_type, content, metadata_json, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, time.time(), "companion_savings", content, metadata_json,
             _db.entry_content_hash("companion_savings", content, metadata_json)),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        _db.note_dropped_write(db_path, "journal_savings", exc)  # never fails the hook


def _journal_write(session_id: str, tokens_est: int, cost_est: float) -> None:
    """Best-effort journal entry — never fails the hook.

    Uses the canonical journal schema from companion._sqlite (shared with
    JournalStore); the hook historically carried a divergent copy of the
    entries DDL and whichever process ran first won the schema race.
    Duplicate deliveries of the same event collapse via the content-hash
    UNIQUE index; dropped writes are logged instead of silently passed.
    """
    db_path = Path(os.environ.get(
        "TOKENPAK_COMPANION_JOURNAL_DIR",
        str(Path.home() / ".tokenpak" / "companion"),
    )) / "journal.db"
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = _db.connect(db_path)
        _db.ensure_journal_schema(conn)
        content = f"pre-send: ~{tokens_est:,} tokens, est ${cost_est:.4f}"
        metadata_json = json.dumps({"tokens_est": tokens_est, "cost_est": cost_est})
        conn.execute(
            "INSERT OR IGNORE INTO entries "
            "(session_id, timestamp, entry_type, content, metadata_json, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, time.time(), "auto", content, metadata_json,
             _db.entry_content_hash("auto", content, metadata_json)),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        _db.note_dropped_write(db_path, "journal_entry", exc)  # never fails the hook


def _record_cost(session_id: str, tokens_est: int, cost_est: float) -> None:
    """Best-effort pre-send cost row — never fails the hook.

    Upserts ONE 'estimate' row per (session, day), refreshed in place to
    the latest full-transcript estimate. The pre-send token estimate is
    cumulative (whole transcript // 4), so inserting a row per prompt made
    a session's rows grow monotonically and the daily gate summed the
    series — wildly over-counting daily spend. One refreshed row per
    session is equivalent to recording per-turn deltas against a
    high-water mark: the day's total equals the sum of true marginal
    estimates and never exceeds the final transcript estimate.

    Output tokens are unknown pre-send, so only input is recorded; the
    recording planes contribute 'actual' rows, which the gate prefers.
    """
    import datetime
    db_path = Path(os.environ.get(
        "TOKENPAK_COMPANION_JOURNAL_DIR",
        str(Path.home() / ".tokenpak" / "companion"),
    )) / "budget.db"
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = _db.connect(db_path)
        _db.ensure_costs_schema(conn)
        conn.execute(
            _db.COSTS_ESTIMATE_UPSERT_SQL,
            (time.time(), datetime.date.today().isoformat(), session_id,
             int(max(0, tokens_est)), round(float(max(0.0, cost_est)), 6)),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        _db.note_dropped_write(db_path, "cost_estimate", exc)  # never fails the hook


if __name__ == "__main__":
    sys.exit(main())
