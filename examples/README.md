# TokenPak Examples

Practical, runnable examples for common TokenPak use cases.

## Quick Start

```bash
pip install tokenpak
cd examples/basic_compression
python main.py
```

---

## Examples

### 🟢 Basic (Start Here)

| Example | Problem Solved | Time | Savings |
|---|---|---|---|
| [basic_compression](./basic_compression/) | Compress verbose text/code | 5 min | 40–60% |
| [cache_management](./cache_management/) | Avoid reprocessing identical content | 5 min | 100x speedup on hits |
| [cli_usage](./cli_usage/) | Compress files from the terminal | 5 min | 40–60% |
| [async_compression](./async_compression/) | Non-blocking compression for asyncio apps | 5 min | 40–60% |

### 🟡 Intermediate

| Example | Problem Solved | Time | Savings |
|---|---|---|---|
| [multi_turn_compression](./multi_turn_compression/) | Keep long chat histories within token budgets | 10 min | 40–65% |
| [openai_wrapper](./openai_wrapper/) | Drop-in OpenAI client with auto-compression | 10 min | 30–60% |
| [claude_integration](./claude_integration/) | Drop-in Anthropic client with auto-compression | 10 min | 30–60% |
| [streaming_compression](./streaming_compression/) | Compress log/file streams on-the-fly | 10 min | 40–65% |
| [error_handling](./error_handling/) | Graceful fallbacks, retries, circuit breakers | 10 min | N/A |

### 🔴 Advanced

| Example | Problem Solved | Time | Savings |
|---|---|---|---|
| [api_server](./api_server/) | Compression proxy server for any LLM app | 15 min | 30–60% |
| [fastapi_middleware](./fastapi_middleware/) | Auto-compress request bodies in FastAPI | 15 min | 30–60% |
| [flask_integration](./flask_integration/) | Decorator + before_request hook for Flask | 15 min | 30–60% |
| [django_integration](./django_integration/) | Middleware + service layer for web apps | 15 min | 30–60% |
| [langchain_integration](./langchain_integration/) | LangChain memory + RAG document compression | 15 min | 40–60% |

### 🌍 Real-World Scenarios

| Example | Problem Solved | Time | Savings |
|---|---|---|---|
| [real_world/vector_compression.py](./real_world/vector_compression.py) | Compress RAG retrieval chunks to fit token budget | 10 min | 40–55% |
| [real_world/db_query_compression.py](./real_world/db_query_compression.py) | Compress database result text fields | 10 min | 40–65% |
| [real_world/api_response_compression.py](./real_world/api_response_compression.py) | Compress third-party API responses | 10 min | 50–75% |

### 📊 Benchmarking

| Example | Problem Solved | Time | |
|---|---|---|---|
| [performance_benchmarking](./performance_benchmarking/) | Measure savings, speed, and cost impact | 5 min | — |

---

## REST API Examples

If you're using TokenPak as an HTTP service (see [api_server](./api_server/)):

```bash
# Start the server
cd api_server && python server.py

# Then run cURL examples
bash api_server/curl_examples.sh
```

Or use Python requests directly:

```python
import requests

r = requests.post("http://localhost:8000/compress", json={
    "text": "Your verbose text here...",
})
print(r.json()["compressed"])
```

---

## Integration Patterns

### Standalone CLI
```bash
tokenpak compress input.txt -o output.txt
tokenpak compress --stdin < large_file.txt
```

### Programmatic (Python)
```python
from tokenpak import HeuristicEngine
engine = HeuristicEngine()
compressed = engine.compact(text)
```

### Async (asyncio)
```python
import asyncio
from concurrent.futures import ThreadPoolExecutor
_executor = ThreadPoolExecutor()

async def compress(text):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, engine.compact, text)
```

### HTTP API
```python
import requests
result = requests.post("http://localhost:8000/compress", json={"text": text}).json()
```

---

## Error Handling

Always wrap compression in try/except for production:

```python
def safe_compress(text: str) -> str:
    try:
        return engine.compact(text)
    except Exception:
        return text  # fallback: original text
```

See [error_handling/](./error_handling/) for full patterns including retry, circuit breaker, and timeout.

---

## Performance

Typical results on a modern CPU:
- **Latency:** ~1ms per compression call
- **Throughput:** ~1,000 req/s single-threaded
- **Savings:** 40-60% on prose, 30-50% on code, 45-65% on chat history

Run the benchmark:
```bash
cd performance_benchmarking && python main.py
```
