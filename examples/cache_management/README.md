# Cache Management Example

**Problem:** Compressing or processing the same content repeatedly wastes compute and time.

**Solution:** TokenPak's `CacheManager` stores results with configurable TTLs. Repeated requests return instantly from cache.

## What This Shows

- Basic cache get/set/hit patterns
- Content-addressed caching (hash-based keys for deduplication)
- TTL expiry behavior

## Expected Results

| Scenario | Latency |
|---|---|
| First compression (cache miss) | 5–20ms |
| Repeated compression (cache hit) | <0.1ms |

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
=== Compression Cache Demo ===

Doc 1: ❌ Cache MISS — compressed 900→350 chars in 8.3ms, cached for 5min
Doc 2: ❌ Cache MISS — compressed 675→280 chars in 5.1ms, cached for 5min
Doc 3: ✅ Cache HIT  — 350 chars (saved recompression!)

Summary: 1 hits, 2 misses — 33% hit rate
```

## Key API

```python
from tokenpak import CacheManager

cache = CacheManager(default_ttl=300)  # 5-min default TTL

# Store a result
cache.set("my_key", "result_value", ttl=60)

# Retrieve (returns (hit: bool, value))
hit, value = cache.get("my_key")
if hit:
    return value  # instant!
else:
    # compute and cache...
```

## Time to Complete

~5 minutes
