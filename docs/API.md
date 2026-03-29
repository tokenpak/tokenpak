# TokenPak API Reference

**Complete reference for TokenPak SDK classes, adapters, and utilities.**

---

## Quick Navigation

| Use Case | Class | Import |
|----------|-------|--------|
| Compress context | `HeuristicEngine` | `from tokenpak import HeuristicEngine` |
| Track token counts | `CompletionTracker` | `from tokenpak import CompletionTracker` |
| Manage cache | `CacheManager` | `from tokenpak import CacheManager` |
| Validate requests | `RequestValidator` | `from tokenpak.validation import RequestValidator` |
| Anthropic SDK | `AnthropicAdapter` | `from tokenpak.adapters import AnthropicAdapter` |
| OpenAI SDK | `OpenAIAdapter` | `from tokenpak.adapters import OpenAIAdapter` |
| LangChain | `LangChainAdapter` | `from tokenpak.adapters import LangChainAdapter` |
| LiteLLM | `LiteLLMAdapter` | `from tokenpak.adapters import LiteLLMAdapter` |

---

## Core Classes

### HeuristicEngine

Fast, rule-based compression engine. No external dependencies.

```python
from tokenpak import HeuristicEngine
from tokenpak.engines.base import CompactionHints

engine = HeuristicEngine()

# Basic compression
compressed = engine.compact(text)

# With target budget
hints = CompactionHints(target_tokens=2048)
compressed = engine.compact(text, hints)

# Get compression stats
result = engine.compress_with_stats(text)
print(f"Reduction: {result['compression_ratio']:.1%}")
```

**Methods:**
- `compress(text: str) -> str` — Compress text to best effort
- `compact(text: str, hints: CompactionHints) -> str` — Compress with budget constraints
- `compress_with_stats(text: str) -> dict` — Return compressed text + metrics

---

### CompletionTracker

Track API spend, token counts, and latency.

```python
from tokenpak import CompletionTracker

tracker = CompletionTracker()

# Record a completion
tracker.record(
    model="claude-3-5-sonnet-20241022",
    tokens_in=1200,
    tokens_out=300,
    cost_usd=0.0156,
    latency_ms=1250
)

# Summarize stats
summary = tracker.summary()
print(f"Total cost: ${summary['total_cost']:.4f}")
print(f"Requests: {summary['num_requests']}")
print(f"Avg latency: {summary['avg_latency_ms']:.0f}ms")

# Get top models by cost
expensive = tracker.top_models_by_cost(limit=5)
```

**Methods:**
- `record(model, tokens_in, tokens_out, cost_usd, latency_ms=None)`
- `summary() -> dict` — Aggregate statistics
- `top_models_by_cost(limit=5) -> list` — Most expensive models
- `stats_by_model(model: str) -> dict` — Stats for one model

---

### CacheManager

In-process cache with hit-rate tracking.

```python
from tokenpak import CacheManager

cache = CacheManager(ttl_seconds=3600)

# Set and get
cache.set("key1", {"response": "data"})
value = cache.get("key1")

# Stats
stats = cache.stats()
print(f"Hit rate: {stats['hit_rate']:.1%}")

# Clear
cache.clear()
```

**Methods:**
- `set(key: str, value: Any, ttl_seconds: int = None)`
- `get(key: str) -> Any | None`
- `delete(key: str)`
- `clear()`
- `stats() -> dict` — Hit rate, size, evictions

---

### RequestValidator

Validate and normalize incoming LLM requests.

```python
from tokenpak.validation import RequestValidator

validator = RequestValidator()

# Validate request
result = validator.validate({
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 100
})

if result.is_valid:
    print("Request OK")
else:
    print(f"Errors: {result.errors}")
```

**Methods:**
- `validate(request: dict) -> ValidationResult` — Check request validity
- `normalize(request: dict) -> dict` — Normalize to standard format

---

## Adapters

Adapters let you use TokenPak with different LLM SDKs.

### Base Adapter Pattern

All adapters implement:
- `prepare_request(request: dict) -> dict` — Validate and normalize
- `send(request: dict) -> dict` — POST to proxy
- `parse_response(response: dict) -> dict` — Convert response to SDK format
- `extract_tokens(response: dict) -> dict` — Get token counts
- `call(request: dict) -> dict` — Full pipeline

### AnthropicAdapter

Use TokenPak proxy with Anthropic SDK.

```python
from tokenpak.adapters import AnthropicAdapter

adapter = AnthropicAdapter(
    base_url="http://localhost:8766",
    api_key="sk-ant-..."
)

# Full request
response = adapter.call({
    "model": "claude-3-5-sonnet-20241022",
    "max_tokens": 1024,
    "messages": [
        {"role": "user", "content": "Explain quantum computing"}
    ]
})

# Extract tokens
tokens = adapter.extract_tokens(response)
print(f"Input: {tokens['input_tokens']}")
print(f"Output: {tokens['output_tokens']}")
```

### OpenAIAdapter

Use TokenPak proxy with OpenAI SDK.

```python
from tokenpak.adapters import OpenAIAdapter

adapter = OpenAIAdapter(
    base_url="http://localhost:8766/v1",
    api_key="sk-..."
)

response = adapter.call({
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hello"}]
})

tokens = adapter.extract_tokens(response)
```

### LangChainAdapter

Route LangChain requests through TokenPak.

```python
from tokenpak.adapters import LangChainAdapter

adapter = LangChainAdapter(
    base_url="http://localhost:8766",
    api_key="sk-..."
)

# Automatically routes to Anthropic or OpenAI based on provider field
response = adapter.call({
    "provider": "openai",
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hi"}]
})
```

### LiteLLMAdapter

Use TokenPak with LiteLLM-style model strings.

```python
from tokenpak.adapters import LiteLLMAdapter

adapter = LiteLLMAdapter(
    base_url="http://localhost:8766",
    api_key="sk-..."
)

# Provider inferred from model string
response = adapter.call({
    "model": "openai/gpt-4o",
    "messages": [{"role": "user", "content": "Hi"}]
})

# Or Anthropic
response = adapter.call({
    "model": "anthropic/claude-3-5-sonnet-20241022",
    "messages": [{"role": "user", "content": "Hi"}],
    "max_tokens": 512
})
```

---

## Exceptions

### TokenPakAdapterError

Base exception for all adapter errors.

```python
from tokenpak.adapters import TokenPakAdapterError

try:
    response = adapter.call(request)
except TokenPakAdapterError as e:
    print(f"Adapter error (HTTP {e.status_code}): {e.message}")
    print(f"Raw response: {e.raw}")
```

**Attributes:**
- `message: str` — Error description
- `status_code: int | None` — HTTP status code
- `raw: Any` — Raw response body

---

## Common Patterns

### Pattern: Track Costs Per Request

```python
from tokenpak.adapters import AnthropicAdapter
from tokenpak import CompletionTracker
import time

adapter = AnthropicAdapter(...)
tracker = CompletionTracker()

start = time.time()
response = adapter.call(request)
latency_ms = (time.time() - start) * 1000

tokens = adapter.extract_tokens(response)
tracker.record(
    model=request["model"],
    tokens_in=tokens["input_tokens"],
    tokens_out=tokens["output_tokens"],
    cost_usd=compute_cost(tokens),
    latency_ms=latency_ms
)
```

### Pattern: Compress Before Sending

```python
from tokenpak import HeuristicEngine
from tokenpak.engines.base import CompactionHints

engine = HeuristicEngine()

# Compress long context
original_context = "... very long file contents ..."
compressed = engine.compact(
    original_context,
    CompactionHints(target_tokens=2048)
)

# Use in request
response = adapter.call({
    "model": "claude-3-5-sonnet-20241022",
    "messages": [
        {
            "role": "user",
            "content": f"Context:\n{compressed}\n\nQuestion: ?"
        }
    ],
    "max_tokens": 500
})
```

### Pattern: Validate Request Before Sending

```python
from tokenpak.validation import RequestValidator

validator = RequestValidator()
request = {
    "model": "gpt-4",
    "messages": [...],
    "max_tokens": 100
}

result = validator.validate(request)
if not result.is_valid:
    print(f"Invalid request: {result.errors}")
else:
    response = adapter.call(request)
```

---

## Type Hints

Common type patterns used across TokenPak:

- `Optional[T]` — Value may be None
- `Union[A, B]` or `A | B` — Either type accepted
- `list[T]` — List of items of type T
- `dict[K, V]` — Dictionary mapping K → V
- `Any` — Dynamically typed

Example:
```python
def compress(
    text: str,
    hints: CompactionHints | None = None
) -> str:
    ...
```

---

## Module Organization

```
tokenpak/
├── adapters/
│   ├── __init__.py        # AnthropicAdapter, OpenAIAdapter, etc.
│   ├── base.py            # TokenPakAdapter (base class)
│   ├── anthropic.py       # Anthropic SDK adapter
│   ├── openai.py          # OpenAI SDK adapter
│   ├── langchain.py       # LangChain adapter
│   └── litellm.py         # LiteLLM adapter
├── engines/
│   ├── base.py            # CompressionEngine (base), CompactionHints
│   └── heuristic.py       # HeuristicEngine (default)
├── validation/
│   └── request_validator.py  # RequestValidator
├── __init__.py            # Top-level imports
└── telemetry/
    └── ...                # CacheManager, CompletionTracker
```

---

## Getting Help

- **Examples:** See `~/vault/01_PROJECTS/tokenpak/examples/`
- **Tests:** See `~/vault/01_PROJECTS/tokenpak/tests/`
- **Troubleshooting:** See [QUICKSTART.md](./QUICKSTART.md#troubleshooting)
- **Issues:** Open a GitHub issue on [kaywhy331/tokenpak](https://github.com/kaywhy331/tokenpak)

---

**Last updated:** 2026-03-27 | **TokenPak v1.0.2+**
