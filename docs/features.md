---
title: "features"
created: 2026-03-24T19:05:55Z
updated: 2026-04-01
---
# Feature Matrix

All features are **open source** under the **Apache-2.0 license**.

**Last verified:** April 1, 2026

---

## Quick Reference

| Category | Feature | Status | Notes |
|----------|---------|--------|-------|
| **Core Routing** | Multiple provider adapters | ✅ Available | 5 adapters built-in |
| | Fallback chains | ✅ Available | Auto-failover to backup providers |
| | Circuit breaker | ✅ Available | Recovers from rate limits |
| | Custom routing rules | ✅ Available | Model-based routing |
| **Token Management** | Token counting (all providers) | ✅ Available | Native per-provider counting |
| | Cost tracking | ✅ Available | Per-request + aggregated |
| | Budget enforcement | ✅ Available | Auto-reject over budget |
| **Compression** | Deduplication | ✅ Available | Remove repeated content |
| | Document compression | ✅ Available | Compact long docs (20–35% savings) |
| | Instruction table | ✅ Available | Compress repetitive instructions |
| | Dictionary compression | ⚠️ Experimental | Post-compaction dictionary pass |
| | Skeleton extraction | ✅ Available | Structure-preserving compression |
| **Caching** | Semantic (prompt-hash) cache | ✅ Available | LRU with configurable TTL |
| | Cache registry | ✅ Available | Tracks cache entries |
| | Provider prompt caching | 🔜 Planned | Native Anthropic/OpenAI cache hints |
| **Error Handling** | Normalized error messages | ✅ Available | Consistent across providers |
| | Automatic retries | ✅ Available | Exponential backoff, configurable |
| | Error telemetry | ✅ Available | Log error types and frequency |
| **Observability** | Request/response logging | ✅ Available | JSON logs, searchable |
| | Token usage reports | ✅ Available | CSV export, JSON export |
| | CLI dashboard | ✅ Available | `tokenpak dashboard` |
| | Web dashboard | ✅ Available | `http://localhost:8766/dashboard` |
| | Prometheus metrics | ✅ Available | `/metrics` endpoint |
| **Agentic** | Streaming support | ✅ Available | Full SSE passthrough |
| | Error normalization | ✅ Available | Agent-readable error format |
| | Capsule builder | ⚠️ Experimental | Session compression |
| **Vault Integration** | Document indexing | ✅ Available | .md, .txt, .pdf |
| | BM25 search | ✅ Available | Fast keyword search |
| | Auto-injection | ✅ Available | Add relevant docs to context |
| | Symbol extraction | ✅ Available | Functions, classes, variables |
| | AST parsing | ✅ Available | Code structure parsing |
| | Chunk optimization | ✅ Available | Smart chunking |
| | Watcher mode | ✅ Available | Live re-index on file changes |
| **CLI** | `start` command | ✅ Available | Start the proxy |
| | `cost` / `savings` | ✅ Available | View spend and savings |
| | `doctor` | ✅ Available | Diagnostics + auto-fix |
| | `compress` / `preview` | ✅ Available | Test compression |
| | `dashboard` | ✅ Available | Real-time monitoring |
| | `index` / `vault` | ✅ Available | Vault management |

**Status key:** ✅ Available — ⚠️ Experimental — 🔜 Planned

---

## Feature Details

### Core Proxy

Provider routing, adapters, tool schema handling, fallback chains, circuit breaker, streaming, passthrough.

```python
from tokenpak import Client

# Works out-of-the-box
client = Client(api_key="...", model="claude-opus-4-6")
response = client.messages.create(...)
```

### Adaptive MemoryGuard

Built-in memory pressure management that auto-adapts to any machine size:

- Auto-calculates thresholds from system RAM (no config needed)
- Selectively evicts coldest cache entries while preserving hot/recent data
- Monitors both proxy RSS and system-available memory
- Periodic `gc.collect()` + `malloc_trim()` for allocator housekeeping
- `GET /memory` endpoint for real-time status

See [Memory Guard](MEMORY-GUARD.md) for full documentation.

### Token Counting

Accurate token counts across all providers using native tokenizers:

```python
tokens = client.count_tokens(
    model="claude-opus-4-6",
    messages=[{"role": "user", "content": "..."}]
)
```

### Compression

Deduplication, document compression, instruction table, budget tracking, fidelity tiers. Automatically applied to requests above the compaction threshold (default: 4,500 tokens).

Typical savings by content type:

| Content Type | Typical Savings |
|-------------|-----------------|
| Markdown documentation | 20–30% |
| Structured JSON/YAML | 25–40% |
| Python code | 5–15% |
| Conversation prose | 15–25% |
| System prompts | 20–35% |

See [Performance Benchmarks](performance.md) for measured data.

### Caching

TokenPak includes **semantic caching** based on request hashing (model + prompt content). Cache hits return stored responses instantly, eliminating redundant API calls.

- ✅ **Available now:** LRU cache with configurable TTL, cache registry, per-request cache bypass via headers
- 🔜 **Planned:** Native provider prompt caching integration (Anthropic cache hints, OpenAI cached tokens)

In production with agentic workflows (repeated system prompts, tool schemas), cache hit rates of 70–85% are typical.

### Error Handling

Normalized errors and automatic retries with exponential backoff:

```python
# Retries automatically with circuit breaker
response = client.messages.create(...)
# If provider fails, falls back to next in chain
```

### Vault Features

Indexing, search, auto-injection, symbol extraction, AST parsing, chunking, watcher, SQLite backend:

```bash
# Configure via environment variables
export TOKENPAK_INJECT_BUDGET=4000
export TOKENPAK_INJECT_TOP_K=5
export VAULT_INDEX_PATH=~/.tokenpak/index.json
```

---

## Configuration

TokenPak is configured via **environment variables** (canonical) with optional file-based config override. See [Configuration](configuration.md) for the complete reference.

Minimal production config:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export TOKENPAK_PORT=8766
tokenpak start
```

---

## Feature Roadmap

### Planned

- Provider prompt caching integration (Anthropic/OpenAI native cache hints)
- Multi-turn conversation history management
- Vision/multimodal compression support
- Advanced batch processing

---

## License

**Apache-2.0.** All features are free and open source. Use however you like.

See the [GitHub repository](https://github.com/tokenpak/tokenpak) for the full license text.
