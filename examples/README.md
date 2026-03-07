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

### 🟡 Intermediate

| Example | Problem Solved | Time | Savings |
|---|---|---|---|
| [multi_turn_compression](./multi_turn_compression/) | Keep long chat histories within token budgets | 10 min | 40–65% |
| [openai_wrapper](./openai_wrapper/) | Drop-in OpenAI client with auto-compression | 10 min | 30–60% |
| [claude_integration](./claude_integration/) | Drop-in Anthropic client with auto-compression | 10 min | 30–60% |
| [streaming_compression](./streaming_compression/) | Compress log/file streams on-the-fly | 10 min | 40–65% |

### 🔴 Advanced

| Example | Problem Solved | Time | Savings |
|---|---|---|---|
| [api_server](./api_server/) | Compression proxy server for any LLM app | 15 min | 30–60% |
| [django_integration](./django_integration/) | Middleware + service layer for web apps | 15 min | 30–60% |
| [langchain_integration](./langchain_integration/) | LangChain memory + RAG document compression | 15 min | 40–60% |

---

## Coverage

These examples cover the most common TokenPak use cases:

- ✅ Basic text/code compression
- ✅ Caching (deduplication, TTL)
- ✅ Multi-turn conversation management
- ✅ OpenAI API integration
- ✅ Anthropic/Claude integration
- ✅ LangChain pipeline integration
- ✅ Streaming/real-time compression
- ✅ REST API server pattern
- ✅ Web framework middleware (Django/FastAPI)
- ✅ CLI usage

---

## Common Questions

**Q: What's the typical compression ratio?**
A: 40–60% on prose, 30–50% on code. Depends heavily on how verbose the input is.

**Q: Does compression lose information?**
A: HeuristicEngine removes filler sentences and redundant comments while preserving
meaning. Code blocks, headers, and high-signal content are always kept.

**Q: Is caching automatic?**
A: No — you opt in. Use `CacheManager` with a content hash as the key.
See [cache_management](./cache_management/) for the pattern.

**Q: Which example should I start with?**
A: [basic_compression](./basic_compression/) — it's 5 minutes and shows the core API.

---

## Key APIs Used in These Examples

```python
from tokenpak import HeuristicEngine, CacheManager
from tokenpak.engines.base import CompactionHints

# Compress text
engine = HeuristicEngine()
compressed = engine.compact(text)
compressed = engine.compact(text, CompactionHints(target_tokens=200))

# Cache results
cache = CacheManager(default_ttl=300)
cache.set("key", value, ttl=60)
hit, value = cache.get("key")
```

---

## Contributing

Found a missing use case? PRs welcome!
See the project [README](../README.md) for contribution guidelines.
