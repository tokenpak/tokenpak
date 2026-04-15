# SPDX-License-Identifier: Apache-2.0
"""Orchestrate a prove run — execute both arms and produce the report.

Flow:
  1. Parse the scenario .md file
  2. Launch live display (tmux panes or terminal windows)
  3. Run Arm A (Direct API) — all turns, streaming to log file
  4. Run Arm B (Through TokenPak) — same turns, streaming to log file
  5. Print comparison report in the triggering terminal
  6. Save results to JSON

Arm A runs first so its requests don't warm the provider's cache for Arm B.
This makes the comparison conservative (Arm B gets zero unfair advantage).
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

from .arm import ArmResult, TurnResult, run_arm
from .display import LiveDisplay
from .reporter import format_report, save_result
from .scenario import Scenario


def run_proof(
    scenario: Scenario,
    live: bool = True,
    output_dir: Path | None = None,
) -> tuple[ArmResult, ArmResult, str]:
    """Execute a full prove run: both arms + report.

    Args:
        scenario: The parsed scenario to run.
        live: Whether to launch live display windows.
        output_dir: Override for result storage directory.

    Returns:
        (arm_a_result, arm_b_result, proof_id)
    """
    # Generate proof ID
    proof_id = f"prf_{hashlib.sha1(f'{scenario.name}{time.time()}'.encode()).hexdigest()[:8]}"

    # Log file paths
    log_dir = Path.home() / ".tokenpak" / "prove" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    arm_a_log = log_dir / f"{proof_id}_arm_a.log"
    arm_b_log = log_dir / f"{proof_id}_arm_b.log"

    # ── Header ──────────────────────────────────────────────
    n_turns = len(scenario.turns)
    print(f"\n  tokenpak prove: starting value proof \"{scenario.name}\"", file=sys.stderr)
    print(f"  model: {scenario.model}  |  turns: {n_turns}  |  provider: {scenario.provider}", file=sys.stderr)
    print(f"  proof id: {proof_id}", file=sys.stderr)

    # ── Live display ────────────────────────────────────────
    display = None
    if live:
        display = LiveDisplay(arm_a_log, arm_b_log)
        attach_info = display.start()
        print(f"\n  Live view: {attach_info}", file=sys.stderr)

    print("", file=sys.stderr)

    # ── Arm A: Direct API ───────────────────────────────────
    print("  Running Arm A (Direct API)...", file=sys.stderr)

    def on_turn_a(turn_num: int, result: TurnResult) -> None:
        if result.error:
            print(f"    Turn {turn_num}/{n_turns} ERROR: {result.error}", file=sys.stderr)
        else:
            cache_str = ""
            if result.cache_read_tokens:
                cache_str = f" ({result.cache_read_tokens:,} cached)"
            print(
                f"    Turn {turn_num}/{n_turns} done  "
                f"{result.input_tokens:,} in{cache_str}"
                f" / {result.output_tokens:,} out"
                f" / {result.latency_s:.1f}s"
                f" / ${result.cost_usd:.4f}",
                file=sys.stderr,
            )

    arm_a = run_arm(
        scenario=scenario,
        proxied=False,
        log_path=arm_a_log,
        on_turn_complete=on_turn_a,
    )

    if arm_a.error and not arm_a.turns:
        print(f"\n  Arm A failed: {arm_a.error}", file=sys.stderr)
        if display:
            display.stop()
        return arm_a, ArmResult(arm_name="tokenpak", error="skipped (Arm A failed)"), proof_id

    # Brief pause between arms — let provider rate limits reset
    print("", file=sys.stderr)
    time.sleep(2)

    # ── Arm B: Through TokenPak ─────────────────────────────
    print("  Running Arm B (With TokenPak)...", file=sys.stderr)

    def on_turn_b(turn_num: int, result: TurnResult) -> None:
        if result.error:
            print(f"    Turn {turn_num}/{n_turns} ERROR: {result.error}", file=sys.stderr)
        else:
            cache_str = ""
            if result.cache_read_tokens:
                cache_str = f" ({result.cache_read_tokens:,} cached)"
            print(
                f"    Turn {turn_num}/{n_turns} done  "
                f"{result.input_tokens:,} in{cache_str}"
                f" / {result.output_tokens:,} out"
                f" / {result.latency_s:.1f}s"
                f" / ${result.cost_usd:.4f}",
                file=sys.stderr,
            )

    arm_b = run_arm(
        scenario=scenario,
        proxied=True,
        log_path=arm_b_log,
        on_turn_complete=on_turn_b,
    )

    # ── Report ──────────────────────────────────────────────
    report = format_report(arm_a, arm_b, scenario.name, proof_id)
    print(report, file=sys.stdout)

    # Save result JSON
    result_path = save_result(arm_a, arm_b, scenario.name, proof_id, output_dir)
    print(f"  Saved: {result_path}", file=sys.stderr)

    # Save log paths
    print(f"  Logs:  {arm_a_log}", file=sys.stderr)
    print(f"         {arm_b_log}", file=sys.stderr)

    # ── Cleanup ─────────────────────────────────────────────
    if display:
        print(f"\n  Live display still running — close with: tmux kill-session -t tokenpak-prove", file=sys.stderr)

    print("", file=sys.stderr)

    return arm_a, arm_b, proof_id
