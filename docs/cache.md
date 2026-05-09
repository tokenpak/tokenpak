# Caching: Token Count Cache & Vault Registry

TokenPak uses two distinct caching layers. Neither stores your prompts or responses — both are purely operational.

---

## Token Count Cache (LRU)

### What it is

An in-process LRU (Least Recently Used) cache for token counts. When TokenPak measures a message block to decide whether to compress it, it hashes the content and stores the count. If the same content appears again (common with repeated system prompts or injected context), the count is returned from cache instantly.

### Why it matters

Token counting is the most frequently called operation in the compression pipeline. Without caching, counting the same 10K-token system prompt on every request wastes 50–200ms. With cache, it's a hash lookup: **~0.1ms**.

**Benchmark result: 25x speedup on repeated content.**

### Implementation

```python
from functools import lru_cache

@lru_cache(maxsize=1024)
def count_tokens(content: str, model: str) -> int:
 # Uses tiktoken if available, otherwise a fast heuristic
 ...
```

Cache size: 1,024 entries (configurable). Evicts the least recently used entry when full.

### Accuracy

Token counting uses:
1. **tiktoken** (if installed) — exact OpenAI-compatible token counts
2. **Heuristic fallback** — `len(text.split()) * 1.35` approximation (~5% margin of error)

Install tiktoken for precise counts:

```bash
pip install tokenpak[tiktoken]
```

The heuristic is conservative — it slightly over-estimates, so the compression threshold check errs on the side of compressing when near the boundary.

---

## Vault Registry (Semantic Index Cache)

### What it is

A SQLite database (`~/.tokenpak/registry.db`) that acts as a persistent index of your codebase or notes vault. When you run `tokenpak index ~/vault`, each file is parsed, its content is extracted, and a content-addressed record is written to the registry.

### What's stored

Each record contains:

| Field | Description |
|---|---|
| `path` | Relative file path |
| `content_hash` | SHA-256 of file content |
| `file_type` | Detected type (code, markdown, json, etc.) |
| `tokens` | Estimated token count |
| `symbols` | Extracted identifiers (functions, classes, etc.) |
| `last_indexed` | Timestamp |

**No file content is stored in the registry.** Only metadata and extracted symbols.

### Change detection

On re-index, TokenPak computes the SHA-256 of each file and compares it to the stored hash. Only modified or new files are re-processed. Unchanged files are skipped.

```bash
tokenpak index ~/vault
# Indexing 572 files...
# 541 unchanged (skipped), 31 updated, 2 new → 3.2s
```

Without content hashing, re-indexing a 572-file vault takes ~180s. With it: **~3s**. That's the **55x speedup** from the benchmark.

### Performance tuning

```bash
# Auto-tune worker count for your hardware
tokenpak calibrate ~/vault

# Manual override
tokenpak index ~/vault --workers 8
```

Calibration runs a bounded benchmark (min 1 worker, max `--max-workers`, default 8) and saves the optimal value to `~/.tokenpak/calibration.json`. Subsequent index runs use it automatically.

**Benchmark (572-file vault):**

```
Workers: 1 → 180.2s (baseline)
Workers: 4 → 6.8s
Workers: 8 → 3.3s ← auto-selected on this machine
Throughput: ~2,738 files/sec
```

### Zero-token search

The vault registry enables semantic search without any LLM calls:

```bash
tokenpak vault search "compression benchmark results"
# Returns top-k matching files by relevance score
# 0 tokens, 22.7ms average latency
```

Search uses BM25 term matching over indexed symbols and file paths. No embeddings, no external service.

### File watching

```bash
tokenpak index ~/vault --watch
```

With `--watch`, the indexer monitors the directory for changes using filesystem events. Modified files are re-indexed incrementally in the background. The index stays current without manual re-runs.

As a systemd service:

```bash
systemctl --user start tokenpak-watcher@$(systemd-escape ~/vault)
```

---

## Provider Prompt Caching

TokenPak is compatible with Anthropic's native prompt caching (`cache_control` headers). If your client sends requests with `cache_control`, TokenPak passes them through unchanged.

TokenPak's compression and Anthropic prompt caching are complementary:

1. TokenPak compresses the prompt on your machine (fewer tokens sent)
2. Anthropic caches the compressed prompt at the API level (faster repeat calls)

Combined, these can reduce effective cost by 80%+ on workloads with large, repeated system prompts.

---

## Cache Locations

| Cache | Location | Type | Cleared by |
|---|---|---|---|
| Token count | In-process LRU | Memory | Proxy restart |
| Vault registry | `~/.tokenpak/registry.db` | SQLite | `rm ~/.tokenpak/registry.db` + reindex |
| Calibration | `~/.tokenpak/calibration.json` | JSON | Delete file + recalibrate |
| Telemetry | `~/.tokenpak/telemetry.db` | SQLite | `tokenpak maintenance prune` |
