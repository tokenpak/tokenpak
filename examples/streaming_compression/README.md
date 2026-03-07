# Streaming Compression Example

**Problem:** Real-time data streams (logs, API responses, file reads) produce verbose content continuously. You need to compress it before feeding to an LLM.

**Solution:** `StreamingCompressor` buffers incoming lines, compresses in chunks, and yields compressed output — so you can process arbitrarily large streams without loading everything into memory.

## What This Shows

- Chunk-based streaming compression with `StreamingCompressor`
- Log stream compression (typical DevOps use case)
- File stream compression (codebase feeding)
- Cumulative stats across chunks

## Setup

```bash
pip install tokenpak
```

## Run

```bash
python main.py
```

## Sample Output

```
=== Log Stream Compression ===

Processing 60 log lines in chunks of 15...

Chunk 1: 15 lines, 145 → 62 tokens (57.2% savings)
Chunk 2: 15 lines, 138 → 58 tokens (58.0% savings)
Chunk 3: 15 lines, 141 → 60 tokens (57.4% savings)
Chunk 4: 15 lines, 133 → 56 tokens (57.9% savings)

Cumulative: {'total_tokens_in': 557, 'total_tokens_out': 236, 'total_savings_pct': 57.6}
```

## Key API

```python
compressor = StreamingCompressor(
    chunk_lines=20,      # Lines to buffer before compressing
    target_tokens=200,   # Target tokens per compressed chunk
    overlap_lines=2,     # Lines carried over for context continuity
)

# Compress any line iterator
for compressed_chunk, stats in compressor.compress_stream(log_lines):
    print(f"Saved {stats['savings_pct']}%")
    feed_to_llm(compressed_chunk)

# Get cumulative stats
print(compressor.cumulative_stats)
```

## Use Cases

- Compressing application logs before LLM analysis
- Feeding large codebases to LLMs in chunks
- Real-time monitoring dashboards with LLM summaries
- Continuous RAG pipelines

## Time to Complete

~10 minutes
