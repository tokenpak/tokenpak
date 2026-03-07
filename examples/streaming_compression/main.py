"""
Streaming Compression Example
===============================
Compress content from streaming sources (file streams, log tails, API streams)
using TokenPak's HeuristicEngine on chunks or accumulated buffers.

Problem: Real-time data streams (logs, live APIs, file tails) produce
         verbose content continuously. You need to compress it on-the-fly
         before feeding to an LLM.

Solution: A StreamingCompressor that buffers, compresses in chunks,
          and yields compressed output with stats.

Expected savings: 40-65% on verbose log/prose streams.
Setup: pip install tokenpak
"""

import sys
import os
import io
import time
from typing import Iterator, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from tokenpak import HeuristicEngine
from tokenpak.engines.base import CompactionHints


# ---------------------------------------------------------------------------
# Streaming Compressor
# ---------------------------------------------------------------------------

class StreamingCompressor:
    """
    Compresses text streams chunk by chunk.

    Buffers incoming lines, compresses when buffer reaches a threshold,
    and yields compressed chunks with savings stats.

    Usage:
        compressor = StreamingCompressor(chunk_lines=20)
        for compressed_chunk, stats in compressor.compress_stream(log_lines):
            feed_to_llm(compressed_chunk)
    """

    def __init__(
        self,
        chunk_lines: int = 20,
        target_tokens: int = 200,
        overlap_lines: int = 2,
    ):
        """
        Args:
            chunk_lines: Lines to buffer before compressing
            target_tokens: Target token count per compressed chunk
            overlap_lines: Lines to carry over between chunks (preserves context)
        """
        self.engine = HeuristicEngine()
        self.chunk_lines = chunk_lines
        self.target_tokens = target_tokens
        self.overlap_lines = overlap_lines

        self._total_in = 0
        self._total_out = 0

    def compress_chunk(self, lines: list[str]) -> tuple[str, dict]:
        """
        Compress a list of lines.

        Returns:
            (compressed_text, stats_dict)
        """
        text = "\n".join(lines)
        hints = CompactionHints(target_tokens=self.target_tokens)
        compressed = self.engine.compact(text, hints)

        tokens_in = len(text) // 4
        tokens_out = len(compressed) // 4
        self._total_in += tokens_in
        self._total_out += tokens_out

        return compressed, {
            "lines": len(lines),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "savings_pct": round(max(0, 1 - tokens_out / max(1, tokens_in)) * 100, 1),
        }

    def compress_stream(
        self, lines: Iterator[str]
    ) -> Iterator[tuple[str, dict]]:
        """
        Consume a line iterator, yield compressed chunks with stats.

        Args:
            lines: Iterator of text lines

        Yields:
            (compressed_chunk, stats)
        """
        buffer = []
        overlap = []

        for line in lines:
            buffer.append(line.rstrip())

            if len(buffer) >= self.chunk_lines:
                # Prepend overlap from previous chunk
                chunk = overlap + buffer
                compressed, stats = self.compress_chunk(chunk)
                yield compressed, stats

                # Carry over last N lines for context continuity
                overlap = buffer[-self.overlap_lines:] if self.overlap_lines else []
                buffer = []

        # Flush remaining buffer
        if buffer:
            chunk = overlap + buffer
            compressed, stats = self.compress_chunk(chunk)
            yield compressed, stats

    @property
    def cumulative_stats(self) -> dict:
        """Total compression stats across all chunks."""
        return {
            "total_tokens_in": self._total_in,
            "total_tokens_out": self._total_out,
            "total_savings_pct": round(
                max(0, 1 - self._total_out / max(1, self._total_in)) * 100, 1
            ),
        }


# ---------------------------------------------------------------------------
# Demo scenarios
# ---------------------------------------------------------------------------

def simulate_log_stream(n_lines: int = 60) -> Iterator[str]:
    """Generate realistic-looking log lines for testing."""
    import random
    random.seed(42)

    levels = ["INFO", "DEBUG", "WARN", "ERROR"]
    services = ["auth", "db", "api", "cache", "worker"]
    messages = [
        "Request received for endpoint /api/v1/users with params {ms}ms",
        "Database query executed in {ms}ms: SELECT * FROM users WHERE active=true",
        "Cache miss for key user_session_{id}, fetching from database",
        "Authentication token validated successfully for user {id}",
        "Rate limit check passed: 45/100 requests used in current window",
        "Background job completed: sent {n} email notifications",
        "Health check: all services responding normally",
        "Connection pool: 8/20 connections active",
    ]

    for i in range(n_lines):
        level = random.choice(levels)
        service = random.choice(services)
        msg = random.choice(messages).format(
            ms=random.randint(10, 500),
            id=random.randint(1000, 9999),
            n=random.randint(1, 100),
        )
        yield f"2026-03-06 18:{i//60:02d}:{i%60:02d} [{level:5}] {service:6}: {msg}"


def demo_log_compression():
    """Compress a simulated log stream."""
    print("=== Log Stream Compression ===\n")

    compressor = StreamingCompressor(chunk_lines=15, target_tokens=100)
    log_lines = list(simulate_log_stream(60))

    print(f"Processing {len(log_lines)} log lines in chunks of 15...\n")

    chunks_processed = 0
    for i, (compressed, stats) in enumerate(compressor.compress_stream(iter(log_lines))):
        chunks_processed += 1
        print(f"Chunk {i+1}: {stats['lines']} lines, "
              f"{stats['tokens_in']} → {stats['tokens_out']} tokens "
              f"({stats['savings_pct']}% savings)")
        if i == 0:
            print(f"\n  Sample compressed output:\n  {compressed[:200].replace(chr(10), chr(10)+'  ')}\n")

    print(f"\nCumulative: {compressor.cumulative_stats}")


def demo_file_stream_compression():
    """Compress a file stream (useful for feeding codebases to LLMs)."""
    print("\n=== File Stream Compression ===\n")

    # Create a sample verbose Python file in memory
    sample_code = """
# Configuration Module
# This module handles all configuration loading and validation
# It reads from environment variables and config files

import os
import json
from pathlib import Path

# Default configuration values
# These are used when no environment variable is set
DEFAULT_HOST = "localhost"   # Default server host
DEFAULT_PORT = 8080          # Default server port  
DEFAULT_DEBUG = False        # Debug mode disabled by default

def load_config():
    \"\"\"
    Load configuration from environment and config files.
    
    This function reads configuration from multiple sources:
    1. Default values (lowest priority)
    2. Config file if it exists
    3. Environment variables (highest priority)
    
    Returns a dictionary with all configuration values.
    \"\"\"
    # Start with defaults
    config = {
        "host": DEFAULT_HOST,
        "port": DEFAULT_PORT,
        "debug": DEFAULT_DEBUG,
    }
    
    # Override with config file if present
    config_path = Path("config.json")
    if config_path.exists():
        with open(config_path) as f:
            file_config = json.load(f)
        config.update(file_config)
    
    # Override with environment variables if set
    if os.environ.get("APP_HOST"):
        config["host"] = os.environ["APP_HOST"]
    if os.environ.get("APP_PORT"):
        config["port"] = int(os.environ["APP_PORT"])
    if os.environ.get("APP_DEBUG"):
        config["debug"] = os.environ["APP_DEBUG"].lower() == "true"
    
    return config  # Return the final merged configuration
""" * 3  # Make it long enough to chunk

    lines = sample_code.split("\n")
    compressor = StreamingCompressor(chunk_lines=25, target_tokens=150)

    print(f"File: {len(lines)} lines, ~{len(sample_code)//4} tokens total")

    all_compressed = []
    for compressed, stats in compressor.compress_stream(iter(lines)):
        all_compressed.append(compressed)

    final = "\n---\n".join(all_compressed)
    print(f"Compressed: ~{len(final)//4} tokens")
    print(f"Total savings: {compressor.cumulative_stats['total_savings_pct']}%")


if __name__ == "__main__":
    demo_log_compression()
    demo_file_stream_compression()
    print("\n✅ Streaming compression example complete!")
