#!/usr/bin/env python3
"""
Basic TokenPak Compression Example

This script demonstrates how to use TokenPak to compress long text
while staying within a token budget. Shows compression ratio and
before/after token counts.

Usage:
    python3 basic_compression.py                    # Use sample text
    python3 basic_compression.py --file myfile.txt  # Compress a file
    python3 basic_compression.py --budget 1024      # Custom token budget
    python3 basic_compression.py --output out.txt   # Save to file
"""

import argparse
import sys
from pathlib import Path

try:
    from tokenpak import HeuristicEngine
    from tokenpak.engines.base import CompactionHints
except ImportError:
    print("Error: TokenPak not installed. Run: pip install tokenpak[ml,tiktoken]")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Compress text using TokenPak",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        help="Input file to compress (default: use sample text)",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=2048,
        help="Target token budget (default: 2048)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Save compressed output to file",
    )

    args = parser.parse_args()

    # Load input text
    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                text = f.read()
            print(f"📖 Loaded: {args.file}")
        except FileNotFoundError:
            print(f"Error: File not found: {args.file}")
            sys.exit(1)
    else:
        # Use sample text (documentation about TokenPak)
        text = """
        TokenPak is a context compression library designed for LLM agents.
        It reduces token usage by 40-60% while preserving semantic meaning.
        
        The library works with any LLM provider: OpenAI, Anthropic, Google,
        local models, and more. It uses heuristic-based compression to identify
        and preserve important information while removing redundancy.
        
        Key features include:
        - Deterministic compression (same input = same output)
        - Budget-aware (respects your token limits)
        - Provider-agnostic (works with any LLM)
        - Fast heuristic engine (no ML overhead)
        - Optional ML-based compression for higher quality
        
        Common use cases:
        - Chat history compression for long conversations
        - Document retrieval augmentation
        - Multi-turn dialogue context management
        - Batch processing with fixed token budgets
        - RAG (Retrieval-Augmented Generation) systems
        
        Installation is simple: pip install tokenpak
        
        The API is straightforward: create an engine, define your budget,
        and compress your text. The compressed output is deterministic,
        making it safe for reproducible LLM workflows.
        """ * 5  # Repeat to make it longer
        print("📖 Using sample text (5x repetition for demonstration)")

    # Initialize compression engine
    print(f"⚙️  Initializing HeuristicEngine...")
    engine = HeuristicEngine()

    # Define compression hints
    hints = CompactionHints(target_tokens=args.budget)

    # Perform compression
    print(f"🔄 Compressing to {args.budget} tokens...")
    compressed = engine.compact(text, hints)

    # Calculate statistics
    original_words = len(text.split())
    compressed_words = len(compressed.split())
    savings = 100 * (1 - len(compressed) / len(text))

    # Display results
    print("\n" + "=" * 60)
    print("COMPRESSION RESULTS")
    print("=" * 60)
    print(f"Original size:    {len(text):,} chars | ~{original_words:,} words")
    print(f"Compressed size:  {len(compressed):,} chars | ~{compressed_words:,} words")
    print(f"Space savings:    {savings:.1f}%")
    print("=" * 60)

    # Show preview of compressed text
    print("\n📄 Compressed Output Preview:")
    print("-" * 60)
    preview = (compressed[:300] + "...") if len(compressed) > 300 else compressed
    print(preview)
    print("-" * 60)

    # Save to file if requested
    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(compressed)
            print(f"\n✅ Saved compressed output to: {args.output}")
        except IOError as e:
            print(f"Error: Could not write to {args.output}: {e}")
            sys.exit(1)

    print("✅ Compression complete!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
