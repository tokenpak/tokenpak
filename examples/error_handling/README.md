# Error Handling Example

**Problem:** Production LLM apps can't crash on compression failure. Compressing bad input or a flaky service should degrade gracefully.

**Solution:** Five battle-tested patterns for robust compression.

## What This Shows

| Pattern | When to Use |
|---|---|
| `safe_compress` | Any production code — never raises |
| `compress_with_retry` | Network-backed compression service |
| `validated_compress` | Strict input enforcement with clear errors |
| `CompressionCircuitBreaker` | High-volume service protecting against cascading failures |
| `compress_with_timeout` | Real-time apps with hard latency SLAs |

## Setup

```bash
pip install -r requirements.txt
python main.py
```

## Key Patterns

### Always-Safe Compression

```python
def safe_compress(text: str) -> str:
    try:
        return engine.compact(text)
    except Exception:
        return text  # always return something usable
```

### Circuit Breaker

```python
cb = CompressionCircuitBreaker(failure_threshold=5, recovery_timeout=30.0)
result = cb.compress(text)  # auto-degrades if compression keeps failing
```

### Timeout Guard

```python
result = compress_with_timeout(text, timeout_seconds=2.0)
# Returns original if compression takes longer than 2 seconds
```
