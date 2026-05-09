# SPDX-License-Identifier: Apache-2.0
"""Orchestrate a prove run — execute all arms and produce the report.

Supports two modes:

  **Legacy (2-arm)**: scenario has no ``matrix:`` — runs direct vs proxied
  for the single model/provider defined in the frontmatter.

  **Matrix (N-arm)**: scenario has a ``matrix:`` list — runs every
  (platform, provider, model) combination defined, each as a separate arm.

Arms run sequentially.  Each arm gets its own log file for live display.
The triggering terminal shows per-turn progress for every arm, then
the full comparison report at the end.
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

from .adapter import ArmConfig, ArmResult, TurnResult
from .adapter import run_arm as adapter_run_arm
from .display import LiveDisplay
from .reporter import format_matrix_report, save_result
from .scenario import Scenario


def _build_arms(scenario: Scenario) -> list[ArmConfig]:
    """Build the list of arms to execute from the scenario."""
    if scenario.matrix:
        arms = []
        for entry in scenario.matrix:
            arms.append(ArmConfig(
                name=entry.get("name", f"{entry.get('provider', '?')}/{entry.get('model', '?')}"),
                platform=entry.get("platform", "api"),
                provider=entry.get("provider", scenario.provider),
                model=entry.get("model", scenario.model),
                via_tokenpak=entry.get("via_tokenpak", entry.get("platform") == "proxy"),
                base_url=entry.get("base_url", ""),
                api_key_env=entry.get("api_key_env", ""),
                format=entry.get("format", ""),
                cli_command=entry.get("cli_command", ""),
            ))
        return arms

    # Legacy: two arms — direct + proxy
    return [
        ArmConfig(name="Direct API", platform="api",
                  provider=scenario.provider, model=scenario.model),
        ArmConfig(name="With TokenPak", platform="proxy",
                  provider=scenario.provider, model=scenario.model,
                  via_tokenpak=True),
    ]


def run_proof(
    scenario: Scenario,
    live: bool = True,
    output_dir: Path | None = None,
) -> tuple[list[ArmResult], str]:
    """Execute a full prove run: all arms + report.

    Returns:
        (list_of_arm_results, proof_id)
    """
    proof_id = f"prf_{hashlib.sha1(f'{scenario.name}{time.time()}'.encode()).hexdigest()[:8]}"

    arms = _build_arms(scenario)
    n_arms = len(arms)
    n_turns = len(scenario.turns)

    log_dir = Path.home() / ".tokenpak" / "prove" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # ── Header ──────────────────────────────────────────────
    print(f"\n  tokenpak prove: \"{scenario.name}\"", file=sys.stderr)
    print(f"  turns: {n_turns}  |  arms: {n_arms}  |  proof: {proof_id}", file=sys.stderr)
    for i, arm in enumerate(arms):
        tp = " + tokenpak" if arm.via_tokenpak else ""
        print(f"    [{i+1}] {arm.name:24s}  {arm.platform}/{arm.provider}/{arm.model}{tp}", file=sys.stderr)
    print("", file=sys.stderr)

    # ── Live display (first two arms for split pane) ────────
    display = None
    if live and n_arms >= 2:
        log_a = log_dir / f"{proof_id}_arm_1.log"
        log_b = log_dir / f"{proof_id}_arm_2.log"
        display = LiveDisplay(log_a, log_b)
        attach_info = display.start()
        print(f"  Live view: {attach_info}", file=sys.stderr)
        print("", file=sys.stderr)

    # ── Execute arms sequentially ───────────────────────────
    results: list[ArmResult] = []

    for i, arm_cfg in enumerate(arms):
        arm_num = i + 1
        log_path = log_dir / f"{proof_id}_arm_{arm_num}.log"

        tp_tag = " + TokenPak" if arm_cfg.via_tokenpak else ""
        print(f"  [{arm_num}/{n_arms}] {arm_cfg.name}{tp_tag}...", file=sys.stderr)

        def on_turn(turn_num: int, tr: TurnResult, _arm=arm_num) -> None:
            if tr.error:
                print(f"    Turn {turn_num}/{n_turns} ERROR: {tr.error}", file=sys.stderr)
            else:
                cache = f" ({tr.cache_read_tokens:,} cached)" if tr.cache_read_tokens else ""
                print(
                    f"    Turn {turn_num}/{n_turns} done  "
                    f"{tr.input_tokens:,} in{cache}"
                    f" / {tr.output_tokens:,} out"
                    f" / {tr.latency_s:.1f}s"
                    f" / ${tr.cost_usd:.4f}",
                    file=sys.stderr,
                )

        arm_result = adapter_run_arm(
            cfg=arm_cfg,
            turns=scenario.turns,
            system=scenario.system,
            max_tokens=scenario.max_tokens,
            log_path=log_path,
            on_turn_complete=on_turn,
        )
        results.append(arm_result)

        if arm_result.error and not arm_result.turns:
            print(f"    FAILED: {arm_result.error}", file=sys.stderr)

        # Brief pause between arms
        if i < n_arms - 1:
            print("", file=sys.stderr)
            time.sleep(2)

    # ── Report ──────────────────────────────────────────────
    report = format_matrix_report(results, scenario.name, proof_id)
    print(report, file=sys.stdout)

    result_path = save_result(results, scenario.name, proof_id, output_dir)
    print(f"  Saved: {result_path}", file=sys.stderr)

    log_files = [log_dir / f"{proof_id}_arm_{i+1}.log" for i in range(n_arms)]
    print(f"  Logs:  {log_files[0]}", file=sys.stderr)
    for lf in log_files[1:]:
        print(f"         {lf}", file=sys.stderr)

    if display:
        print("\n  Live display still running — close with: tmux kill-session -t tokenpak-prove", file=sys.stderr)

    print("", file=sys.stderr)
    return results, proof_id
