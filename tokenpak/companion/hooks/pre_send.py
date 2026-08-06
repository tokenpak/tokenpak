#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""UserPromptSubmit hook — ultra-lean pre-send pipeline.

Performance critical: this runs on EVERY prompt.  The synchronous path must
never wait on a SQLite writer lock, WAL checkpoint, or database fsync.  The
installed bash hook targets < 50ms; this Python fallback is otherwise bounded
by interpreter startup plus one atomic write-ahead-intent enqueue.

Design choices for speed:
    - No tiktoken (char//4 heuristic is within 3% per stress test)
    - No transcript parsing (os.path.getsize is instant)
    - No heavy imports (stdlib + companion._sqlite, which is itself
      stdlib-only shared SQLite plumbing; the parent packages are already
      imported by the ``-m`` invocation)
    - Journal + cost persistence is one crash-replayable atomic intent;
      SQLite materialisation happens outside the prompt's critical path
    - Budget check uses a read-only SQLite snapshot plus pending intents,
      so asynchronous persistence cannot open an under-count window

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

from tokenpak.companion import _sqlite as _db
from tokenpak.companion import config as _companion_config

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
    try:
        budget = float(os.environ.get("TOKENPAK_COMPANION_BUDGET", "0"))
    except (TypeError, ValueError):
        budget = 0.0
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
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "decision": "block",
                        "reason": msg,
                    }
                }
            )
        )
        return 2

    # Journal write (best-effort, non-blocking). Log even when tokens_est is 0
    # so we still record that a cycle fired — useful for detecting silent
    # failures. Fabricate a session_id from PID+time if hook_input lacks one
    # (e.g. some older Claude CLI builds omit it in --print mode).
    if not session_id:
        session_id = f"anon-{os.getpid()}-{int(time.time())}"
    # Queue the journal entry and cost estimate as ONE atomic intent.  A
    # detached singleton worker materialises it into both SQLite stores; the
    # budget reader includes pending intents, so there is no under-count gap.
    # Recorded AFTER the gate read above, so this cycle is counted exactly once.
    _queue_pre_send(session_id, tokens_est, cost_est)

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
        run_dir = _companion_config.journal_run_dir()
        run_dir.mkdir(parents=True, exist_ok=True)
        # pid-unique temp name so two concurrent hook processes can't
        # interleave writes to the same temp file before the atomic rename.
        tmp = run_dir / f"current-session.{os.getpid()}.tmp"
        tmp.write_text(session_id.strip(), encoding="utf-8")
        tmp.replace(run_dir / "current-session")
    except Exception:
        pass  # never fail the hook


def _get_daily_total() -> float:
    """Read today's truthful spend without mutating SQLite.

    Per (session, day): sums actual rows when present, otherwise takes the
    latest estimate (companion._sqlite.DAILY_SPEND_SQL) — the gate reads
    true marginal spend, never estimate + actual for the same traffic and
    never a summed series of cumulative transcript estimates.
    """
    import datetime

    # Read across homes: a pre-canonical install has its spend history in the
    # legacy tree, and treating "file absent here" as zero spend would open
    # the budget gate on a user who has already spent.
    write_dir = _companion_config.journal_write_dir()
    db_path = _companion_config.resolve_journal_file("budget.db") or (write_dir / "budget.db")
    try:
        return _db.daily_spend_with_pending(
            db_path,
            pending_base_dir=write_dir,
            date=datetime.date.today().isoformat(),
        )
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
    base_dir = _companion_config.journal_write_dir()
    db_path = base_dir / "journal.db"
    try:
        import datetime

        timestamp = time.time()
        meta = {
            "tool": tool,
            "tokens_avoided": int(max(0, tokens_avoided)),
            "cost_avoided_usd": float(max(0.0, cost_avoided_usd)),
        }
        content = f"{tool}: -{meta['tokens_avoided']:,} tokens (~${meta['cost_avoided_usd']:.4f})"
        _db.queue_pre_send_event(
            base_dir,
            session_id=session_id,
            timestamp=timestamp,
            date=datetime.date.fromtimestamp(timestamp).isoformat(),
            entry_type="companion_savings",
            content=content,
            metadata_json=json.dumps(meta),
            tokens_est=None,
            cost_est=None,
        )
        _db.request_async_pre_send_flush(base_dir)
    except Exception as exc:
        _db.note_dropped_write(db_path, "journal_savings", exc)  # never fails the hook


def _queue_pre_send(session_id: str, tokens_est: int, cost_est: float) -> None:
    """Queue one replayable journal + cost intent; never fail the hook.

    SQLite is intentionally absent from this function.  The atomic intent is
    the durable handoff to the worker; journal content-hash dedupe and the
    timestamp-ordered estimate upsert make replay safe if either database
    commit is interrupted.
    """
    import datetime

    base_dir = _companion_config.journal_write_dir()
    db_path = base_dir / "journal.db"
    try:
        timestamp = time.time()
        content = f"pre-send: ~{tokens_est:,} tokens, est ${cost_est:.4f}"
        metadata_json = json.dumps({"tokens_est": tokens_est, "cost_est": cost_est})
        _db.queue_pre_send_event(
            base_dir,
            session_id=session_id,
            timestamp=timestamp,
            date=datetime.date.fromtimestamp(timestamp).isoformat(),
            entry_type="auto",
            content=content,
            metadata_json=metadata_json,
            tokens_est=tokens_est,
            cost_est=cost_est,
        )
        _db.request_async_pre_send_flush(base_dir)
    except Exception as exc:
        _db.note_dropped_write(db_path, "pre_send_intent", exc)  # never fails the hook


if __name__ == "__main__":
    sys.exit(main())
