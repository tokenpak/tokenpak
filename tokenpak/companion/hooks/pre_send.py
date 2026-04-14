#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""UserPromptSubmit hook — the automatic pre-send pipeline.

This script is invoked by Claude Code as a hook before each prompt is sent.
It reads JSON from stdin, runs the pipeline, and either allows (exit 0) or
blocks (exit 2) the send.

Pipeline stages:
    1. Parse hook input (session_id, transcript_path, message)
    2. Estimate tokens from transcript
    3. Simulate cost
    4. Check budget gate
    5. Write journal entry
    6. Print cost estimate to stderr (visible in TUI)

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

import json
import sys
from pathlib import Path


def main() -> int:
    """Hook entry point.  Returns 0 (allow) or 2 (block)."""
    # Read hook input from stdin
    try:
        raw = sys.stdin.read()
        hook_input = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        hook_input = {}

    session_id = hook_input.get("session_id", "")
    transcript_path = hook_input.get("transcript_path", "")

    # Stage 1: Load config
    from ..config import CompanionConfig

    config = CompanionConfig.from_env()
    if not config.enabled:
        return 0  # companion disabled — pass through

    # Stage 2: Estimate tokens from transcript
    tokens_est = 0
    if transcript_path:
        try:
            from ..transcript.parser import parse_transcript

            summary = parse_transcript(transcript_path)
            tokens_est = summary.tokens_est
        except Exception:
            pass  # fail-open

    # Stage 3: Cost simulation
    cost_est = None
    if tokens_est > 0:
        try:
            from ..budget.tracker import BudgetTracker

            tracker = BudgetTracker(
                db_path=config.journal_dir / "budget.db",
                daily_budget=config.budget_daily_usd,
            )
            cost_est = tracker.estimate(input_tokens=tokens_est)
        except Exception:
            pass  # fail-open

    # Stage 4: Budget gate
    if cost_est and cost_est.over_budget:
        # Block the send
        msg = (
            f"tokenpak: budget exceeded "
            f"(${cost_est.daily_total_usd:.2f} / ${cost_est.daily_budget_usd:.2f} daily)"
        )
        print(msg, file=sys.stderr)
        output = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "decision": "block",
                "reason": msg,
            }
        }
        print(json.dumps(output))
        return 2

    # Stage 5: Journal entry
    if session_id:
        try:
            from ..journal.store import JournalStore

            journal = JournalStore(db_path=config.journal_dir / "journal.db")
            journal.add_entry(
                session_id=session_id,
                entry_type="auto",
                content=f"pre-send: ~{tokens_est:,} tokens, est ${cost_est.estimated_cost_usd:.4f}" if cost_est else f"pre-send: ~{tokens_est:,} tokens",
                metadata={"tokens_est": tokens_est, "cost_est": cost_est.estimated_cost_usd if cost_est else 0},
            )
        except Exception:
            pass  # fail-open

    # Stage 6: Print cost estimate to stderr (TUI visibility)
    if config.show_cost and tokens_est > 0:
        parts = [f"tokenpak: ~{tokens_est:,} tokens"]
        if cost_est:
            parts.append(f"est ${cost_est.estimated_cost_usd:.4f}")
            if config.budget_daily_usd > 0:
                pct = cost_est.daily_total_usd / config.budget_daily_usd * 100
                if pct > 50:
                    parts.append(f"budget {pct:.0f}%")
        print("  ".join(parts), file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
