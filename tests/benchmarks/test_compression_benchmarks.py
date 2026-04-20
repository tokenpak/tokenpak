"""
tests/benchmarks/test_compression_benchmarks.py — TokenPak Compression Benchmark Suite

Measures compression ratio, latency, and throughput across:
- 5 payload sizes: 100, 500, 1k, 5k, 10k tokens
- 3 compression modes: none, light, aggressive

First run: generates tests/benchmarks/baseline.json
Subsequent runs: regression check — fails if any mode is >20% slower than baseline

Usage:
    pytest tests/benchmarks/test_compression_benchmarks.py -v
    pytest tests/benchmarks/test_compression_benchmarks.py -v -s  # print table

Generated: 2026-03-24
Author: Cali (TPK-BENCH-01)
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

# ---------------------------------------------------------------------------
# Path setup (allow running from repo root or tests/ dir)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tokenpak.compression.pipeline import CompressionPipeline  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BASELINE_PATH = Path(__file__).parent / "baseline.json"
REGRESSION_THRESHOLD = 1.20  # 20% slower → fail
MIN_LATENCY_FLOOR_MS = (
    1.0  # sub-ms baselines are dominated by OS scheduling jitter; always pass if under this
)
WARMUP_RUNS = 2
MEASURE_RUNS = 5

PAYLOAD_SIZES = [100, 500, 1_000, 5_000, 10_000]

MODES: Dict[str, Dict[str, bool]] = {
    "none": dict(
        enable_dedup=False,
        enable_alias=False,
        enable_segmentation=False,
        enable_directives=False,
        enable_instruction_table=False,
    ),
    "light": dict(
        enable_dedup=True,
        enable_alias=False,
        enable_segmentation=True,
        enable_directives=False,
        enable_instruction_table=False,
    ),
    "aggressive": dict(
        enable_dedup=True,
        enable_alias=True,
        enable_segmentation=True,
        enable_directives=True,
        enable_instruction_table=True,
    ),
}


# ---------------------------------------------------------------------------
# Payload factory
# ---------------------------------------------------------------------------
def _make_messages(target_tokens: int) -> List[Dict[str, Any]]:
    """
    Build a realistic conversation that will compress meaningfully.

    Uses a repeated entity name ("TokenPakAssistant") so alias compression
    has something to act on, plus enough duplicate turns for dedup to trigger.
    """
    SYSTEM = (
        "You are TokenPakAssistant, a helpful AI assistant for the TokenPakAssistant platform. "
        "TokenPakAssistant helps developers optimize their LLM token usage through compression. "
        "When users ask about TokenPakAssistant features, always refer to the TokenPakAssistant docs. "
        "The TokenPakAssistant platform is built for enterprise use. "
        "TokenPakAssistant supports OpenAI, Anthropic, and Google providers."
    )
    FILLER = (
        " TokenPakAssistant configuration options include rate limiting, retry logic, "
        "timeout settings, compression thresholds, and per-model routing rules."
    )

    msgs: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": "How does TokenPakAssistant handle compression for large context windows?",
        },
        {
            "role": "assistant",
            "content": (
                "TokenPakAssistant uses a multi-stage pipeline. The TokenPakAssistant compression "
                "engine applies deduplication, alias compression, and directive application. "
                "TokenPakAssistant can reduce context by 30-80% depending on content type."
            ),
        },
        {
            "role": "user",
            "content": "Can TokenPakAssistant compress tool call responses? What are the limits?",
        },
        {
            "role": "assistant",
            "content": (
                "Yes, TokenPakAssistant compresses tool responses. TokenPakAssistant processes "
                "each tool_result message and applies the TokenPakAssistant compression rules. "
                "The TokenPakAssistant system handles nested JSON, code blocks, and markdown."
            ),
        },
    ]

    # Pad to target by appending duplicate-ish turns (triggers dedup on larger payloads)
    while True:
        estimated = sum(len(m["content"]) // 4 for m in msgs)
        if estimated >= target_tokens:
            break
        msgs.append(
            {
                "role": "user",
                "content": f"Tell me more about TokenPakAssistant.{FILLER}",
            }
        )
        msgs.append(
            {
                "role": "assistant",
                "content": f"TokenPakAssistant provides advanced features.{FILLER}",
            }
        )

    return msgs


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------
def _run_benchmark(
    mode_name: str, mode_kwargs: Dict[str, bool], messages: List[Dict[str, Any]]
) -> Tuple[float, float, float]:
    """
    Run compression MEASURE_RUNS times (after WARMUP_RUNS) and return
    (median_ms, compression_ratio, savings_pct).
    """
    pipeline = CompressionPipeline(**mode_kwargs)

    # Warmup
    for _ in range(WARMUP_RUNS):
        pipeline.run(messages)

    latencies: List[float] = []
    last_result = None
    for _ in range(MEASURE_RUNS):
        t0 = time.perf_counter()
        last_result = pipeline.run(messages)
        latencies.append((time.perf_counter() - t0) * 1000)

    assert last_result is not None
    median_ms = statistics.median(latencies)
    ratio = last_result.tokens_raw / max(last_result.tokens_after, 1)
    return median_ms, ratio, last_result.savings_pct


# ---------------------------------------------------------------------------
# Load / save baseline
# ---------------------------------------------------------------------------
def _load_baseline() -> Dict[str, Any]:
    if BASELINE_PATH.exists():
        return json.loads(BASELINE_PATH.read_text())
    return {}


def _save_baseline(data: Dict[str, Any]) -> None:
    BASELINE_PATH.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Markdown table printer
# ---------------------------------------------------------------------------
def _print_table(results: Dict[str, Dict[str, Any]]) -> None:
    header = f"{'Size':>6} | {'Mode':<10} | {'Raw':>6} | {'After':>6} | {'Ratio':>6} | {'Median ms':>9} | {'Savings':>7}"
    sep = "-" * len(header)
    print("\n" + sep)
    print("TokenPak Compression Benchmark Results")
    print(sep)
    print(header)
    print(sep)
    for key, r in sorted(results.items()):
        size, mode = key.split("|")
        print(
            f"{size:>6} | {mode:<10} | {r['raw']:>6} | {r['after']:>6} | "
            f"{r['ratio']:>5.2f}x | {r['median_ms']:>8.1f}ms | {r['savings_pct']:>6.1f}%"
        )
    print(sep + "\n")


# ---------------------------------------------------------------------------
# Pytest parametrize
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("target_tokens", PAYLOAD_SIZES)
@pytest.mark.parametrize("mode_name", list(MODES.keys()))
def test_compression_benchmark(target_tokens: int, mode_name: str) -> None:
    """Benchmark + regression check for one (mode, size) combination."""
    messages = _make_messages(target_tokens)
    mode_kwargs = MODES[mode_name]

    median_ms, ratio, savings_pct = _run_benchmark(mode_name, mode_kwargs, messages)

    # Record result for table (stored in module-level dict for summary printout)
    key = f"{target_tokens:>6}|{mode_name}"
    _RESULTS[key] = {
        "raw": sum(len(m["content"]) // 4 for m in messages),
        "after": int(sum(len(m["content"]) // 4 for m in messages) / ratio),
        "ratio": ratio,
        "median_ms": median_ms,
        "savings_pct": savings_pct,
    }

    baseline = _load_baseline()
    baseline_key = f"{target_tokens}_{mode_name}"

    if baseline_key not in baseline:
        # First run — write baseline, don't fail
        baseline[baseline_key] = {
            "median_ms": median_ms,
            "ratio": ratio,
            "savings_pct": savings_pct,
        }
        _save_baseline(baseline)
        print(
            f"\n  [BASELINE SET] {target_tokens} tokens / {mode_name}: "
            f"{median_ms:.1f}ms, {ratio:.2f}x, {savings_pct:.1f}% savings"
        )
        return

    # Regression check: latency must not exceed baseline × threshold
    # Floor prevents false failures when baseline is sub-ms (OS scheduling jitter dominates)
    baseline_ms = baseline[baseline_key]["median_ms"]
    allowed_ms = max(baseline_ms * REGRESSION_THRESHOLD, MIN_LATENCY_FLOOR_MS)

    assert median_ms <= allowed_ms, (
        f"REGRESSION: {target_tokens} tokens / {mode_name} mode — "
        f"median latency {median_ms:.1f}ms exceeds baseline "
        f"{baseline_ms:.1f}ms × {REGRESSION_THRESHOLD} = {allowed_ms:.1f}ms"
    )


# ---------------------------------------------------------------------------
# Module-level results dict (populated during test run for summary)
# ---------------------------------------------------------------------------
_RESULTS: Dict[str, Dict[str, Any]] = {}


def test_print_summary() -> None:
    """Print markdown table of all results. Must run after other benchmarks."""
    if _RESULTS:
        _print_table(_RESULTS)
    # This test always passes — it's just a reporter
    assert True
