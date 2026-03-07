"""
Basic Compression Example
=========================
Demonstrates TokenPak's HeuristicEngine for compressing text content.

Problem: LLM context windows are expensive. Verbose text wastes tokens.
Solution: HeuristicEngine compresses text while preserving meaning.

Expected compression: 40-60% reduction in typical prose/code.
Setup: pip install tokenpak
"""

import sys
import os

# Add tokenpak to path if running from examples directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from tokenpak import HeuristicEngine
from tokenpak.engines.base import CompactionHints


def compress_prose():
    """Compress verbose prose text."""
    print("=== Prose Compression ===\n")

    engine = HeuristicEngine()

    verbose_text = """
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
    """

    compressed = engine.compact(verbose_text)

    original_tokens = len(verbose_text) // 4
    compressed_tokens = len(compressed) // 4

    print(f"Original:   ~{original_tokens} tokens ({len(verbose_text)} chars)")
    print(f"Compressed: ~{compressed_tokens} tokens ({len(compressed)} chars)")
    print(f"Savings:    {1 - compressed_tokens/original_tokens:.0%}\n")
    print("--- Compressed Output ---")
    print(compressed)


def compress_with_target():
    """Compress to a specific token target."""
    print("\n=== Targeted Compression (100 tokens) ===\n")

    engine = HeuristicEngine()

    long_text = "\n".join([
        f"Step {i}: This is instruction number {i} explaining how to complete the task."
        for i in range(1, 20)
    ])

    hints = CompactionHints(target_tokens=100)
    compressed = engine.compact(long_text, hints)

    print(f"Original:   ~{len(long_text) // 4} tokens")
    print(f"Compressed: ~{len(compressed) // 4} tokens")
    print(f"Target was: 100 tokens\n")
    print("--- Compressed Output ---")
    print(compressed)


def compress_code():
    """Compress verbose Python code (remove redundant comments)."""
    print("\n=== Code Compression ===\n")

    engine = HeuristicEngine()

    verbose_code = '''
def calculate_statistics(numbers):
    """
    This function calculates basic statistics for a list of numbers.
    It computes the mean, minimum, maximum, and count of the input list.
    The function accepts a list of numeric values as input.
    """
    # Initialize variables to hold our computed statistics
    count = len(numbers)   # Count total number of elements
    total = sum(numbers)   # Sum all elements together
    mean = total / count   # Divide sum by count to get mean

    # Find the minimum value by iterating through the list
    minimum = min(numbers)  # Built-in min function

    # Find the maximum value
    maximum = max(numbers)  # Built-in max function

    # Return a dictionary containing all computed statistics
    return {
        "count": count,    # Number of data points
        "mean": mean,      # Average value
        "min": minimum,    # Smallest value
        "max": maximum,    # Largest value
    }
'''

    compressed = engine.compact(verbose_code)
    print(f"Original:   ~{len(verbose_code) // 4} tokens")
    print(f"Compressed: ~{len(compressed) // 4} tokens")
    print(f"Savings:    {1 - len(compressed)/len(verbose_code):.0%}\n")
    print("--- Compressed Output ---")
    print(compressed)


if __name__ == "__main__":
    compress_prose()
    compress_with_target()
    compress_code()
    print("\n✅ Basic compression example complete!")
