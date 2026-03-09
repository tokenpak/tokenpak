"""
basic_compression.py — TokenPak SDK Quick Start Example
========================================================
Demonstrates core compression features using the public HeuristicEngine API.

What this script shows:
  1. Load sample text (or read from a file)
  2. Compress with default settings
  3. Compress with a specific token budget
  4. Display before/after token counts and savings
  5. Save compressed output to a file

Usage:
  python3 examples/basic_compression.py
  python3 examples/basic_compression.py --file path/to/your/file.txt

Requirements:
  pip install tokenpak
"""

import argparse
import os
import sys

# Allow running from the repo root or the examples/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tokenpak import HeuristicEngine
from tokenpak.engines.base import CompactionHints

# ---------------------------------------------------------------------------
# Sample text — used when no file is provided
# ---------------------------------------------------------------------------
SAMPLE_TEXT = """
The TokenPak library provides a comprehensive solution for managing token budgets
in large language model applications. It includes multiple compression strategies,
caching mechanisms, and telemetry tools. The library is designed to be easy to use
while providing powerful functionality for advanced users. By compressing content,
you can fit more information into fewer tokens, reducing API costs and improving
response quality. The heuristic engine uses rule-based text processing to remove
redundant content while preserving the most important information.

Furthermore, it is worth noting that the library has been carefully engineered
to handle edge cases gracefully. The compression algorithm preserves code blocks,
headers, and list items — elements that tend to carry high informational value.
Sentences that are deemed to be low-signal filler are removed automatically.
This makes it well-suited for compressing chat histories, retrieved documents,
and multi-turn dialogue context before it reaches the model.
"""


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token (GPT-style)."""
    return max(1, len(text) // 4)


def load_text(file_path: str | None) -> str:
    """Load text from a file or return sample text."""
    if file_path:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    return SAMPLE_TEXT


def print_stats(label: str, original: str, compressed: str) -> None:
    """Print before/after token counts and savings."""
    orig_tokens = estimate_tokens(original)
    comp_tokens = estimate_tokens(compressed)
    savings_pct = (1 - comp_tokens / orig_tokens) * 100
    print(f"  Original:   ~{orig_tokens:,} tokens  ({len(original):,} chars)")
    print(f"  Compressed: ~{comp_tokens:,} tokens  ({len(compressed):,} chars)")
    print(f"  Savings:    {savings_pct:.0f}%")
    print()


def save_output(text: str, output_path: str) -> None:
    """Save compressed text to a file."""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"  💾 Saved to: {output_path}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="TokenPak basic compression demo")
    parser.add_argument("--file", help="Path to a text file to compress (optional)")
    parser.add_argument("--budget", type=int, default=None, help="Target token budget")
    parser.add_argument("--output", help="Save compressed output to this path")
    args = parser.parse_args()

    text = load_text(args.file)
    engine = HeuristicEngine()

    # --- Default compression (no budget) ---
    print("=" * 50)
    print("1. Default Compression")
    print("=" * 50)
    compressed_default = engine.compact(text)
    print_stats("default", text, compressed_default)
    print("--- Compressed Output ---")
    print(compressed_default.strip())
    print()

    # --- Targeted compression (with token budget) ---
    budget = args.budget or 80
    print("=" * 50)
    print(f"2. Targeted Compression (budget: {budget} tokens)")
    print("=" * 50)
    hints = CompactionHints(target_tokens=budget)
    compressed_targeted = engine.compact(text, hints)
    print_stats("targeted", text, compressed_targeted)
    print("--- Compressed Output ---")
    print(compressed_targeted.strip())
    print()

    # --- Save output if requested ---
    if args.output:
        save_output(compressed_targeted, args.output)

    print("✅  Basic compression example complete!")


if __name__ == "__main__":
    main()
