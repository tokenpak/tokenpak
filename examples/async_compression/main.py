"""
Async Compression Example
=========================
Demonstrates TokenPak compression in async Python applications.

Problem: Modern Python apps use asyncio, but naive compression blocks the event loop.
Solution: Run TokenPak compression in a thread pool executor to stay non-blocking.

Expected compression: varies by input; measure in your own workflow.
Setup: pip install tokenpak
"""

import asyncio
import time
import sys
import os
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from tokenpak import HeuristicEngine
from tokenpak.engines.base import CompactionHints


# Global executor — reuse across requests for efficiency
_executor = ThreadPoolExecutor(max_workers=4)
_engine = HeuristicEngine()


async def compress_async(text: str, hints: CompactionHints = None) -> str:
    """
    Compress text asynchronously without blocking the event loop.
    Runs the CPU-bound compression in a thread pool.
    """
    loop = asyncio.get_running_loop()
    if hints:
        result = await loop.run_in_executor(
            _executor, lambda: _engine.compact(text, hints=hints)
        )
    else:
        result = await loop.run_in_executor(_executor, lambda: _engine.compact(text))
    return result


async def process_batch(texts: list[str]) -> list[dict]:
    """Compress multiple texts concurrently — true parallelism."""
    print("=== Batch Async Compression ===\n")

    tasks = [compress_async(t) for t in texts]
    start = time.perf_counter()
    results = await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - start

    output = []
    for i, (original, compressed) in enumerate(zip(texts, results)):
        original_tokens = len(original) // 4
        compressed_tokens = len(compressed) // 4
        savings = 1 - compressed_tokens / original_tokens if original_tokens > 0 else 0
        output.append({"index": i, "savings": savings, "compressed": compressed})
        print(f"  [{i}] ~{original_tokens}→{compressed_tokens} tokens ({savings:.0%} savings)")

    print(f"\n  Compressed {len(texts)} texts in {elapsed:.3f}s\n")
    return output


async def pipeline_example():
    """
    Simulate a real async pipeline: fetch → compress → process.
    """
    print("=== Async Pipeline Simulation ===\n")

    async def fetch_data(item_id: int) -> str:
        """Simulate async fetch (e.g., from DB or API)."""
        await asyncio.sleep(0.01)  # simulate I/O
        return f"""
        Item {item_id}: This is a verbose description of an item retrieved from the database.
        It contains extensive detail that was useful for the writer but adds significant
        verbosity for LLM consumption. The item was created on January 1, 2025, and has
        been updated multiple times since then. Various team members have contributed notes
        and observations that, while individually useful, collectively create a large context
        that exceeds typical LLM budget constraints. The core information is: ID={item_id}.
        """

    async def process_item(item_id: int) -> dict:
        """Fetch, compress, and process in one async pipeline."""
        raw = await fetch_data(item_id)
        compressed = await compress_async(raw)
        return {
            "id": item_id,
            "original_tokens": len(raw) // 4,
            "compressed_tokens": len(compressed) // 4,
        }

    # Process 10 items concurrently
    tasks = [process_item(i) for i in range(10)]
    results = await asyncio.gather(*tasks)

    total_original = sum(r["original_tokens"] for r in results)
    total_compressed = sum(r["compressed_tokens"] for r in results)
    print(f"  Processed {len(results)} items concurrently")
    print(f"  Total tokens: {total_original} → {total_compressed} ({1 - total_compressed/total_original:.0%} saved)\n")


async def streaming_async():
    """Compress an incoming async stream of text chunks."""
    print("=== Async Streaming Compression ===\n")

    async def text_stream():
        """Simulate receiving text chunks from an async source."""
        chunks = [
            "The quarterly financial report demonstrates significant improvement...",
            "Revenue increased by approximately 23% compared to the prior year period...",
            "This exceptional growth was driven primarily by expansion in the enterprise segment...",
            "Customer acquisition costs declined meaningfully as brand recognition improved...",
            "The management team remains cautiously optimistic about continued performance...",
        ]
        for chunk in chunks:
            await asyncio.sleep(0.005)
            yield chunk

    buffer = []
    async for chunk in text_stream():
        buffer.append(chunk)

    full_text = " ".join(buffer)
    compressed = await compress_async(full_text)
    print(f"  Streamed {len(buffer)} chunks → compressed as one document")
    print(f"  {len(full_text) // 4} → {len(compressed) // 4} tokens ({1 - len(compressed)/len(full_text):.0%} saved)\n")


async def main():
    sample_texts = [
        """The machine learning model training process is a computationally intensive
        operation that requires significant hardware resources. The process involves
        iteratively adjusting model parameters based on gradient descent optimization
        to minimize a loss function defined over the training dataset.""",

        """In conclusion, the aforementioned findings suggest that, in the overwhelming
        majority of cases studied, the implementation of the proposed solution leads to
        measurable improvements in system throughput, as evidenced by the benchmark results
        presented in the preceding sections of this comprehensive technical report.""",

        """Please note that it is very important to ensure that all configuration
        settings are properly set before running the application. Failure to do so
        may result in unexpected behavior. It is strongly recommended that users
        carefully read all documentation prior to deployment.""",
    ]

    await process_batch(sample_texts)
    await pipeline_example()
    await streaming_async()

    print("✅ All async examples complete")
    _executor.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
