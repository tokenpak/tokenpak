"""
Performance Benchmarking Example
=================================
Measure TokenPak compression performance, token savings, and cost impact.

Problem: You need to know if compression is worth it for YOUR data.
Solution: Benchmark suite that measures speed, savings, and projected cost reduction.

Setup: pip install tokenpak
"""

import sys
import os
import time
import statistics

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from tokenpak import HeuristicEngine
from tokenpak.engines.base import CompactionHints


# ---------------------------------------------------------------------------
# Sample datasets
# ---------------------------------------------------------------------------

SAMPLES = {
    "prose": """
    The implementation of artificial intelligence systems in enterprise environments
    presents numerous challenges that must be carefully considered. Organizations
    frequently encounter difficulties related to data quality, model reliability,
    and integration with existing infrastructure. Furthermore, the cost of operating
    large language models at scale represents a significant financial consideration
    that demands careful optimization. It is therefore essential that engineering
    teams develop robust strategies for managing token consumption efficiently.
    """,

    "technical_docs": """
    ## Configuration

    The `config.yaml` file controls all system behavior. Key settings:

    - `model`: The LLM model identifier (e.g., `gpt-4o`, `claude-sonnet-4-6`)
    - `max_tokens`: Maximum tokens per request (default: 4096)
    - `temperature`: Sampling temperature, 0.0-2.0 (default: 0.7)
    - `timeout`: Request timeout in seconds (default: 30)

    It is very important to note that you should ensure all values are properly
    configured before starting the application. Failure to configure properly
    may result in unexpected behavior or errors during runtime execution.
    """,

    "chat_history": """
    User: Hi, I was wondering if you could help me understand how to use the API?
    Assistant: Of course! I'd be happy to help you understand the API. What would you like to know?
    User: Well, I'm not sure where to start. Maybe you could explain the authentication?
    Assistant: Sure! Authentication uses API keys. You include your key in the Authorization header.
    User: Great, and what about rate limits? I've heard those can be tricky.
    Assistant: Rate limits vary by plan. The free tier allows 60 requests per minute. Paid plans start at 3,500 RPM.
    User: That makes sense. One more thing — what's the best way to handle errors?
    Assistant: Use exponential backoff for 429 and 5xx errors. Wrap calls in try/except blocks.
    """,

    "code_comments": """
    def process_data(input_data, config=None):
        # This function is responsible for processing the input data
        # that has been provided to it. It takes the input data and
        # applies various transformations to it based on the configuration
        # settings that are provided. If no configuration is provided,
        # it will use the default configuration settings.
        
        # First, we need to validate the input data to make sure it
        # is in the correct format and meets all requirements
        if not input_data:
            # If the input data is empty or None, we should return None
            # because there is nothing to process
            return None
        
        # Apply the main processing logic here
        result = transform(input_data)
        return result
    """,
}

# Cost per 1M tokens (approximate)
MODEL_COSTS = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}


# ---------------------------------------------------------------------------
# Benchmarking functions
# ---------------------------------------------------------------------------

def benchmark_compression_speed(engine: HeuristicEngine, text: str, iterations: int = 100) -> dict:
    """Measure compression latency over many iterations."""
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        engine.compact(text)
        elapsed = time.perf_counter() - start
        times.append(elapsed * 1000)  # ms

    return {
        "iterations": iterations,
        "mean_ms": statistics.mean(times),
        "median_ms": statistics.median(times),
        "p95_ms": sorted(times)[int(iterations * 0.95)],
        "p99_ms": sorted(times)[int(iterations * 0.99)],
        "min_ms": min(times),
        "max_ms": max(times),
        "throughput_rps": 1000 / statistics.mean(times),
    }


def measure_token_savings(engine: HeuristicEngine, text: str) -> dict:
    """Measure token reduction for a single text."""
    original_tokens = len(text) // 4  # rough approximation
    compressed = engine.compact(text)
    compressed_tokens = len(compressed) // 4

    savings_tokens = original_tokens - compressed_tokens
    savings_pct = (savings_tokens / original_tokens * 100) if original_tokens > 0 else 0

    return {
        "original_chars": len(text),
        "compressed_chars": len(compressed),
        "original_tokens": original_tokens,
        "compressed_tokens": compressed_tokens,
        "savings_tokens": savings_tokens,
        "savings_pct": savings_pct,
    }


def project_cost_savings(savings_pct: float, monthly_tokens: int) -> dict:
    """
    Project monthly cost savings across models.
    
    Args:
        savings_pct: Token reduction percentage (e.g., 45.0 for 45%)
        monthly_tokens: Current monthly input token volume
    """
    projections = {}
    for model, costs in MODEL_COSTS.items():
        current_cost = (monthly_tokens / 1_000_000) * costs["input"]
        tokens_after = monthly_tokens * (1 - savings_pct / 100)
        new_cost = (tokens_after / 1_000_000) * costs["input"]
        monthly_savings = current_cost - new_cost
        projections[model] = {
            "current_monthly_cost": round(current_cost, 2),
            "new_monthly_cost": round(new_cost, 2),
            "monthly_savings": round(monthly_savings, 2),
            "annual_savings": round(monthly_savings * 12, 2),
        }
    return projections


# ---------------------------------------------------------------------------
# Main benchmark suite
# ---------------------------------------------------------------------------

def run_benchmarks():
    engine = HeuristicEngine()

    print("=" * 60)
    print("TokenPak Performance Benchmark")
    print("=" * 60)

    # --- 1. Token savings by content type ---
    print("\n📊 Token Savings by Content Type\n")
    print(f"{'Type':<20} {'Original':>10} {'Compressed':>12} {'Savings%':>10} {'Chars In':>10} {'Chars Out':>10}")
    print("-" * 75)

    all_savings = []
    for name, text in SAMPLES.items():
        stats = measure_token_savings(engine, text)
        all_savings.append(stats["savings_pct"])
        print(
            f"  {name:<18} {stats['original_tokens']:>10} {stats['compressed_tokens']:>12} "
            f"{stats['savings_pct']:>9.1f}% {stats['original_chars']:>10} {stats['compressed_chars']:>10}"
        )

    avg_savings = statistics.mean(all_savings)
    print(f"\n  Average savings: {avg_savings:.1f}%")

    # --- 2. Compression speed ---
    print("\n⚡ Compression Speed (100 iterations)\n")
    sample_text = SAMPLES["prose"]
    speed = benchmark_compression_speed(engine, sample_text, iterations=100)
    print(f"  Mean latency:   {speed['mean_ms']:.2f}ms")
    print(f"  Median latency: {speed['median_ms']:.2f}ms")
    print(f"  P95 latency:    {speed['p95_ms']:.2f}ms")
    print(f"  P99 latency:    {speed['p99_ms']:.2f}ms")
    print(f"  Throughput:     {speed['throughput_rps']:.0f} req/s")

    # --- 3. Cost projection ---
    print("\n💰 Cost Projection (100M input tokens/month)\n")
    monthly_tokens = 100_000_000
    projections = project_cost_savings(avg_savings, monthly_tokens)

    print(f"  {'Model':<22} {'Current/mo':>12} {'After/mo':>10} {'Saves/mo':>10} {'Saves/yr':>10}")
    print("  " + "-" * 65)
    for model, p in projections.items():
        print(
            f"  {model:<22} ${p['current_monthly_cost']:>10,.2f} ${p['new_monthly_cost']:>8,.2f} "
            f"${p['monthly_savings']:>8,.2f} ${p['annual_savings']:>8,.2f}"
        )

    # --- 4. Scaling test ---
    print("\n📈 Scaling Test (increasing input size)\n")
    base_text = SAMPLES["prose"].strip()
    print(f"  {'Multiplier':<12} {'Tokens In':>10} {'Tokens Out':>11} {'Savings%':>10} {'Time (ms)':>10}")
    print("  " + "-" * 57)

    for multiplier in [1, 2, 5, 10, 20]:
        text = base_text * multiplier
        start = time.perf_counter()
        compressed = engine.compact(text)
        elapsed = (time.perf_counter() - start) * 1000

        orig_t = len(text) // 4
        comp_t = len(compressed) // 4
        pct = (1 - comp_t / orig_t) * 100 if orig_t > 0 else 0
        print(f"  {multiplier:>3}x {len(text):>8} chars  {orig_t:>8} {comp_t:>10} {pct:>9.1f}% {elapsed:>10.1f}")

    print(f"\n✅ Benchmark complete — avg savings: {avg_savings:.1f}%")


if __name__ == "__main__":
    run_benchmarks()
