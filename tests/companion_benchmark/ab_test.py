#!/usr/bin/env python3
"""A/B test: `tokenpak claude` vs vanilla `claude`

Runs identical prompts through both paths and captures:
  - Input tokens (total sent to API)
  - Output tokens
  - Cache read/creation tokens
  - Cost (USD)
  - Latency (time to first token, total time)
  - Response content (for quality comparison)
  - Number of tool calls (turns)

Usage:
    python3 tests/companion_benchmark/ab_test.py [--model sonnet] [--tasks all]

Requirements:
    - `claude` CLI available in PATH
    - tokenpak companion module installed
    - ANTHROPIC_API_KEY set (or OAuth active)
"""

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RunResult:
    """Metrics from a single A/B run."""
    variant: str  # "vanilla" or "companion"
    task_id: str
    model: str
    prompt: str

    # Token metrics (from stream-json)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    # Cost
    cost_usd: float = 0.0

    # Timing
    total_ms: float = 0.0
    num_turns: int = 0

    # Response
    response_text: str = ""
    response_length: int = 0

    # Hook metrics (companion only)
    hook_fired: bool = False
    hook_stderr: str = ""

    # Errors
    error: str = ""


# ---------------------------------------------------------------------------
# Task definitions — same prompts for both variants
# ---------------------------------------------------------------------------

TASKS = {
    "simple_read": {
        "prompt": "Read tokenpak/companion/__init__.py and summarize what it does in 3 bullet points. Be concise.",
        "max_turns": 5,
        "description": "Simple file read + summarization",
    },
    "multi_file_analysis": {
        "prompt": "Read tokenpak/companion/mcp/tools.py and tokenpak/companion/budget/tracker.py. How does the check_budget tool use the BudgetTracker? Explain the data flow in under 100 words.",
        "max_turns": 8,
        "description": "Multi-file analysis requiring cross-reference",
    },
    "code_edit": {
        "prompt": "In tokenpak/companion/config.py, add a new env var TOKENPAK_COMPANION_LOG_LEVEL that defaults to 'info' and accepts 'debug', 'info', 'warn', 'error'. Add it to the CompanionConfig dataclass and from_env(). Show me the diff when done.",
        "max_turns": 10,
        "description": "Code modification task",
    },
    "grep_and_count": {
        "prompt": "Count how many Python files are in tokenpak/companion/ and how many total lines of code they contain. Report the exact numbers.",
        "max_turns": 8,
        "description": "Exploration + counting task",
    },
    "complex_reasoning": {
        "prompt": "Read tokenpak/companion/hooks/pre_send.py and tokenpak/companion/mcp/server.py. If the hook pipeline takes 500ms on a large transcript, what is the architectural bottleneck? Propose a specific optimization. Be concise — under 150 words.",
        "max_turns": 10,
        "description": "Multi-file read + reasoning + optimization proposal",
    },
}


def run_variant(
    variant: str,
    task_id: str,
    task: dict,
    model: str,
) -> RunResult:
    """Run a single task through either vanilla claude or tokenpak claude."""
    result = RunResult(
        variant=variant,
        task_id=task_id,
        model=model,
        prompt=task["prompt"],
    )

    # Build command
    if variant == "vanilla":
        cmd = ["claude", "-p", task["prompt"]]
    else:
        # Use companion launcher with MCP + hooks + system prompt
        companion_dir = Path(__file__).parent.parent.parent / "tokenpak" / "companion"
        run_dir = Path.home() / ".tokenpak" / "companion" / "run"
        run_dir.mkdir(parents=True, exist_ok=True)

        # Generate companion config files (same as launcher.py does)
        mcp_path = run_dir / "mcp.json"
        settings_path = run_dir / "settings.json"
        prompt_path = run_dir / "companion-prompt.md"

        mcp_path.write_text(json.dumps({
            "mcpServers": {
                "tokenpak-companion": {
                    "type": "stdio",
                    "command": sys.executable,
                    "args": ["-m", "tokenpak.companion.mcp.server"],
                }
            }
        }))

        hook_cmd = f"{sys.executable} -m tokenpak.companion.hooks.pre_send"
        settings_path.write_text(json.dumps({
            "permissions": {"allow": ["mcp__tokenpak-companion__*"]},
            "hooks": {
                "UserPromptSubmit": [{
                    "matcher": "",
                    "hooks": [{"type": "command", "command": hook_cmd}],
                }],
            },
        }))

        prompt_path.write_text(
            "## tokenpak companion\n\n"
            "You have tokenpak companion MCP tools: estimate_tokens, check_budget, "
            "load_capsule, prune_context, journal_read, journal_write, session_info.\n"
            "Use them when relevant to optimize cost and context management.\n"
        )

        cmd = [
            "claude", "-p", task["prompt"],
            "--mcp-config", str(mcp_path),
            "--settings", str(settings_path),
            "--append-system-prompt-file", str(prompt_path),
        ]

    # Common flags
    cmd.extend([
        "--model", model,
        "--max-turns", str(task.get("max_turns", 10)),
        "--output-format", "stream-json",
        "--verbose",
        "--include-hook-events",
        "--permission-mode", "bypassPermissions",
    ])

    # Run
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(Path(__file__).parent.parent.parent),
        )
        result.total_ms = (time.perf_counter() - t0) * 1000
    except subprocess.TimeoutExpired:
        result.error = "timeout (120s)"
        result.total_ms = 120_000
        return result
    except Exception as e:
        result.error = str(e)
        return result

    # Parse stream-json output
    response_parts = []
    for line in proc.stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        obj_type = obj.get("type", "")
        subtype = obj.get("subtype", "")

        if obj_type == "result":
            result.cost_usd = obj.get("total_cost_usd", 0.0) or 0.0
            result.num_turns = obj.get("num_turns", 0) or 0
            result.response_text = obj.get("result", "")
            result.response_length = len(result.response_text)
            # Token usage from result
            usage = obj.get("usage", {})
            if usage:
                result.input_tokens = usage.get("input_tokens", 0) or 0
                result.output_tokens = usage.get("output_tokens", 0) or 0
                result.cache_read_tokens = usage.get("cache_read_input_tokens", 0) or 0
                result.cache_creation_tokens = usage.get("cache_creation_input_tokens", 0) or 0

        elif obj_type == "system" and subtype == "hook_response":
            result.hook_fired = True
            result.hook_stderr = obj.get("stderr", "")[:200]

        elif obj_type == "assistant" and "usage" in obj.get("message", {}):
            # Accumulate usage from assistant messages
            usage = obj["message"]["usage"]
            result.input_tokens += usage.get("input_tokens", 0) or 0
            result.output_tokens += usage.get("output_tokens", 0) or 0
            result.cache_read_tokens += usage.get("cache_read_input_tokens", 0) or 0
            result.cache_creation_tokens += usage.get("cache_creation_input_tokens", 0) or 0

    # Parse stderr for hook output
    if proc.stderr:
        result.hook_stderr = proc.stderr.strip()[:500]

    return result


def print_comparison(task_id: str, vanilla: RunResult, companion: RunResult):
    """Print side-by-side comparison for a single task."""
    desc = TASKS[task_id]["description"]
    print(f"\n{'─' * 70}")
    print(f"  Task: {task_id} — {desc}")
    print(f"{'─' * 70}")

    def delta(v, c, unit="", lower_better=True):
        if v == 0 and c == 0:
            return "—"
        if v == 0:
            return f"{c}{unit}"
        diff = c - v
        pct = diff / v * 100 if v != 0 else 0
        arrow = "v" if (diff < 0 and lower_better) or (diff > 0 and not lower_better) else "^" if diff != 0 else "="
        sign = "+" if diff > 0 else ""
        return f"{sign}{diff}{unit} ({sign}{pct:.1f}%) {arrow}"

    rows = [
        ("Input tokens", f"{vanilla.input_tokens:,}", f"{companion.input_tokens:,}",
         delta(vanilla.input_tokens, companion.input_tokens)),
        ("Output tokens", f"{vanilla.output_tokens:,}", f"{companion.output_tokens:,}",
         delta(vanilla.output_tokens, companion.output_tokens)),
        ("Cache read", f"{vanilla.cache_read_tokens:,}", f"{companion.cache_read_tokens:,}",
         delta(vanilla.cache_read_tokens, companion.cache_read_tokens, lower_better=False)),
        ("Cache creation", f"{vanilla.cache_creation_tokens:,}", f"{companion.cache_creation_tokens:,}",
         delta(vanilla.cache_creation_tokens, companion.cache_creation_tokens)),
        ("Cost (USD)", f"${vanilla.cost_usd:.4f}", f"${companion.cost_usd:.4f}",
         delta(vanilla.cost_usd, companion.cost_usd, unit="")),
        ("Latency (ms)", f"{vanilla.total_ms:.0f}", f"{companion.total_ms:.0f}",
         delta(vanilla.total_ms, companion.total_ms, unit="ms")),
        ("Turns", f"{vanilla.num_turns}", f"{companion.num_turns}",
         delta(vanilla.num_turns, companion.num_turns)),
        ("Response len", f"{vanilla.response_length:,}", f"{companion.response_length:,}",
         delta(vanilla.response_length, companion.response_length, lower_better=False)),
        ("Hook fired", "n/a", str(companion.hook_fired), ""),
    ]

    print(f"\n  {'Metric':<18} {'Vanilla':>14} {'Companion':>14} {'Delta':>24}")
    print(f"  {'─' * 18} {'─' * 14} {'─' * 14} {'─' * 24}")
    for metric, v_val, c_val, d_val in rows:
        print(f"  {metric:<18} {v_val:>14} {c_val:>14} {d_val:>24}")

    if companion.hook_stderr:
        print(f"\n  Hook output: {companion.hook_stderr[:120]}")

    if vanilla.error:
        print(f"\n  Vanilla error: {vanilla.error}")
    if companion.error:
        print(f"\n  Companion error: {companion.error}")


def main():
    parser = argparse.ArgumentParser(description="A/B test: tokenpak claude vs vanilla claude")
    parser.add_argument("--model", default="sonnet", help="Model to use (default: sonnet)")
    parser.add_argument("--tasks", default="all", help="Comma-separated task IDs, or 'all'")
    parser.add_argument("--output", default=None, help="Path to write JSON results")
    args = parser.parse_args()

    task_ids = list(TASKS.keys()) if args.tasks == "all" else args.tasks.split(",")
    model = args.model

    print("=" * 70)
    print(f"  A/B TEST: tokenpak claude vs vanilla claude")
    print(f"  Model: {model}  |  Tasks: {len(task_ids)}  |  {time.strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)

    all_results = []

    for task_id in task_ids:
        if task_id not in TASKS:
            print(f"\n  SKIP: unknown task '{task_id}'")
            continue

        task = TASKS[task_id]
        print(f"\n  Running: {task_id} — {task['description']}")

        # Run vanilla first
        print(f"    [A] vanilla claude ...", end="", flush=True)
        vanilla = run_variant("vanilla", task_id, task, model)
        print(f" {vanilla.total_ms:.0f}ms, ${vanilla.cost_usd:.4f}")

        # Run companion
        print(f"    [B] tokenpak claude ...", end="", flush=True)
        companion = run_variant("companion", task_id, task, model)
        print(f" {companion.total_ms:.0f}ms, ${companion.cost_usd:.4f}")

        print_comparison(task_id, vanilla, companion)
        all_results.append({"task": task_id, "vanilla": vanilla.__dict__, "companion": companion.__dict__})

    # Summary
    print(f"\n{'=' * 70}")
    print("AGGREGATE SUMMARY")
    print(f"{'=' * 70}")

    v_cost = sum(r["vanilla"]["cost_usd"] for r in all_results)
    c_cost = sum(r["companion"]["cost_usd"] for r in all_results)
    v_input = sum(r["vanilla"]["input_tokens"] for r in all_results)
    c_input = sum(r["companion"]["input_tokens"] for r in all_results)
    v_output = sum(r["vanilla"]["output_tokens"] for r in all_results)
    c_output = sum(r["companion"]["output_tokens"] for r in all_results)
    v_cache = sum(r["vanilla"]["cache_read_tokens"] for r in all_results)
    c_cache = sum(r["companion"]["cache_read_tokens"] for r in all_results)
    v_time = sum(r["vanilla"]["total_ms"] for r in all_results)
    c_time = sum(r["companion"]["total_ms"] for r in all_results)

    print(f"\n  {'Metric':<22} {'Vanilla':>14} {'Companion':>14} {'Difference':>14}")
    print(f"  {'─' * 22} {'─' * 14} {'─' * 14} {'─' * 14}")
    print(f"  {'Total cost (USD)':<22} {'${:.4f}'.format(v_cost):>14} {'${:.4f}'.format(c_cost):>14} {'${:.4f}'.format(c_cost - v_cost):>14}")
    print(f"  {'Total input tokens':<22} {v_input:>14,} {c_input:>14,} {c_input - v_input:>+14,}")
    print(f"  {'Total output tokens':<22} {v_output:>14,} {c_output:>14,} {c_output - v_output:>+14,}")
    print(f"  {'Total cache reads':<22} {v_cache:>14,} {c_cache:>14,} {c_cache - v_cache:>+14,}")
    print(f"  {'Total latency (s)':<22} {v_time / 1000:>14.1f} {c_time / 1000:>14.1f} {(c_time - v_time) / 1000:>+14.1f}")

    overhead_pct = ((c_cost - v_cost) / v_cost * 100) if v_cost > 0 else 0
    print(f"\n  Companion cost overhead: {overhead_pct:+.1f}%")
    print(f"  (Overhead = companion system prompt + MCP tool definitions)")

    # Write results
    output_path = args.output or str(Path(__file__).parent / "ab_results.json")
    Path(output_path).write_text(json.dumps(all_results, indent=2, default=str))
    print(f"\n  Full results: {output_path}")


if __name__ == "__main__":
    main()
