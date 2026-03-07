# Async Compression Example

**Problem:** Modern Python apps use asyncio, but CPU-bound compression can block the event loop.

**Solution:** Run TokenPak in a `ThreadPoolExecutor` for non-blocking async compression.

## What This Shows

- Non-blocking compression with `asyncio.get_running_loop().run_in_executor()`
- Concurrent batch compression with `asyncio.gather()`
- Async fetch→compress→process pipeline
- Streaming text aggregation before compression

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
python main.py
```

## Expected Output

```
=== Batch Async Compression ===
  [0] ~55→28 tokens (49% savings)
  [1] ~55→30 tokens (45% savings)
  [2] ~45→25 tokens (44% savings)
  Compressed 3 texts in 0.012s
...
✅ All async examples complete
```

## Key Pattern

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor
from tokenpak import HeuristicEngine

_executor = ThreadPoolExecutor(max_workers=4)
_engine = HeuristicEngine()

async def compress_async(text: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, lambda: _engine.compact(text))
```
