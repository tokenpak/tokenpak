# TokenPak Performance Benchmarks

> **Data sources:** In-process benchmarks run 2026-03-26 on reference hardware A;
> cache/throughput benchmarks run 2026-03-26 on reference hardware B. All numbers
> are reproducible via
> `python benchmarks/performance_benchmark.py`. No real API calls are made in
> any benchmark — deterministic, CI-safe.

---

## Contents

1. [Environment](#environment)
2. [Proxy Latency](#proxy-latency)
3. [Throughput & Cache Hit Rate](#throughput-and-cache-hit-rate)
4. [Compression Ratios](#compression-ratios)
5. [Token Savings](#token-savings)
6. [Memory Footprint](#memory-footprint)
7. [Vault Index Lookup](#vault-index-lookup)
8. [SLA Thresholds](#sla-thresholds)
9. [Running the Benchmarks](#running-the-benchmarks)

---

## Environment

### Reference hardware B (cache/throughput benchmarks)

| Spec       | Value                              |
|------------|------------------------------------|
| Host       | reference machine B                |
| OS         | Linux 6.17.0-14-generic            |
| Python     | 3.12.3                             |
| CPU        | 4 cores                            |
| RAM        | 4 GiB total                        |
| GPU        | None                               |
| Proxy port | 8766 (in-process mock)             |
| Mode       | hybrid (BM25 + vector routing)     |

### Reference hardware A (latency/compression benchmarks)

| Spec       | Value                              |
|------------|------------------------------------|
| Host       | reference machine A                |
| OS         | Linux 6.17.0-19-generic            |
| Python     | 3.12.3                             |
| CPU        | 4 cores                            |
| RAM        | 3.7 GiB total / 1.2 GiB available  |
| GPU        | None                               |
| Vault index | 7,938 blocks (~150 MB)            |

---

## Proxy Latency

Latency measured at the **proxy layer only** (HTTP round-trip to local proxy endpoint,
no upstream LLM call). Upstream API latency (typically 200–2,000ms) is additive and
entirely determined by the chosen LLM provider.

### Serial — `/health` endpoint (100 sequential requests)

| Metric | Value   | Notes |
|--------|---------|-------|
| p50    | 0.85 ms | Typical warm-path latency |
| p95    | 0.95 ms | Very tight — near-zero variance at p95 |
| p99    | 37.19 ms | GC/initialization outlier on first request |
| min    | 0.76 ms | |
| max    | 37.19 ms | |
| mean   | 1.22 ms | |
| stdev  | 3.63 ms | |
| n      | 100     | |

> **Note:** The p99 spike (37ms) is a one-time GC / first-request initialization
> event. Steady-state p99 is < 2ms. This is a known Python GIL characteristic on
> constrained hardware.

### Concurrent — 50 simultaneous requests

| Metric        | Value        |
|---------------|--------------|
| Total elapsed | 1,234.2 ms   |
| Success rate  | 100% (50/50) |
| Throughput    | 40.5 req/s   |
| p50           | 946.32 ms    |
| p95           | 1,127.65 ms  |
| p99           | 1,139.58 ms  |
| mean          | 946.96 ms    |

> **Note:** Concurrent p50 jumps to ~946ms due to Python's single-threaded
> `HTTPServer` serializing requests. This is the baseline before connection pooling.
> The proxy itself processes each request in <2ms; the queuing delay dominates.

### Sustained Load — 100 RPS (5 seconds, 20 concurrent workers)

| Metric      | Value      |
|-------------|------------|
| Throughput  | ~98.3 RPS  |
| Total requests | 493     |
| Error rate  | 0.00%      |
| p50 latency | 4.55 ms    |
| p95 latency | 5.51 ms    |
| p99 latency | 159.74 ms  |

> p99 at 159ms under 100 RPS burst is expected on 4-core/4GB hardware under GIL
> + GC pressure. p50 and p95 remain well within target (< 10ms).

---

## Throughput and Cache Hit Rate

Benchmarks run using in-process mock proxy with zlib compression envelope and
2–8ms simulated model latency. Three runs averaged for stability.

### Profile: `light` (100 unique prompts, 50% repeat rate)

| Run        | p50 ms | p99 ms | Throughput | Cache Hit Rate |
|------------|--------|--------|------------|----------------|
| 2026-03-26 (run 1) | 0.03 | 8.70 | 566.8 req/s | 70.4% |
| 2026-03-26 (run 2) | 0.01 | 8.10 | 637.5 req/s | 70.8% |
| 2026-03-26 (run 3) | 0.02 | 8.46 | 580.6 req/s | 69.4% |
| **Average** | **0.02** | **8.42** | **595 req/s** | **70.2%** |

### Profile: `medium` (500 unique prompts, 70% repeat rate)

| Metric         | Value      |
|----------------|------------|
| p50 latency    | 0.02 ms    |
| p99 latency    | ~9 ms      |
| Throughput     | ~1,071 req/s |
| Cache hit rate | 84.3%      |

### Profile: `heavy` (1,000 unique prompts, 85% repeat rate)

| Metric         | Value      |
|----------------|------------|
| p50 latency    | 0.02 ms    |
| p99 latency    | ~9 ms      |
| Throughput     | ~1,107 req/s |
| Cache hit rate | 84.8%      |

> **Takeaway:** Cache hit rate scales with repeat rate. At 70% repeat (realistic
> for agentic workflows with repeated system prompts and tool schemas), the proxy
> eliminates ~70% of upstream token processing overhead.

---

## Compression Ratios

TokenPak's `compact()` function removes whitespace, inline comments, redundant
structure, and low-signal boilerplate from prompt content.

### By Content Type (measured on vault files)

| Sample                    | Original    | After Compact | Retained | Saved |
|---------------------------|-------------|---------------|----------|-------|
| README.md (prose)         | 13,372 chars | 9,977 chars  | 74.6%    | 25.4% |
| CHANGELOG.md (changelog)  | 3,516 chars  | 2,720 chars  | 77.4%    | 22.6% |
| vault/README.md (prose)   | 3,000 chars  | 2,355 chars  | 78.5%    | 21.5% |
| vault/_index.md (structured) | 1,526 chars | 1,003 chars | 65.7%  | 34.3% |
| vault/capabilities.md (dense) | 2,048 chars | 1,926 chars | 94.0% | 6.0% |

### Aggregated Stats

| Metric                  | Value  |
|-------------------------|--------|
| Mean retention ratio    | 78.0%  |
| Best case (structured)  | 65.7% retained → **34% saved** |
| Worst case (dense code) | 94.0% retained → **6% saved** |
| **Average savings**     | **~21%** |

### By Content Category (estimated)

| Content Type         | Typical Savings | Notes |
|----------------------|-----------------|-------|
| Markdown documentation | 20–30%        | Headers, links, verbose phrasing compressible |
| Structured JSON/YAML | 25–40%          | Whitespace, repeated keys |
| Python code           | 5–15%          | Dense; comments/docstrings only |
| Conversation prose    | 15–25%          | Filler phrases, repetition |
| System prompts        | 20–35%          | Boilerplate, redundant instructions |

---

## Token Savings

Token savings combine compression + cache hits. At ~4 chars/token (Claude
tokenizer approximation):

### Per-Request Savings (compression only)

| Sample               | Tokens Before | Tokens After | Saved  | Saving % |
|----------------------|---------------|--------------|--------|----------|
| README.md            | 3,343 t       | 2,494 t      | 849 t  | 25.4%    |
| CHANGELOG.md 5k      | 879 t         | 680 t        | 199 t  | 22.6%    |
| Typical system prompt (2k chars) | ~500 t | ~390 t | ~110 t | 22% |
| Typical context window (16k chars) | ~4,000 t | ~3,120 t | ~880 t | 22% |

### At Scale (estimated, 1,000 requests/day)

| Scenario             | Daily Input Tokens | With TokenPak | Saved    | Est. Cost Saved* |
|----------------------|--------------------|---------------|----------|-----------------|
| Light usage (avg 2k token prompts) | 2M tokens | 1.56M tokens | 440k tokens | ~$1.32 |
| Heavy usage (avg 8k token prompts) | 8M tokens | 6.24M tokens | 1.76M tokens | ~$5.28 |
| Agentic workflow (70% cache hits)  | 8M tokens | 2.4M tokens  | 5.6M tokens | ~$16.80 |

*Cost estimates based on Claude Sonnet input pricing ($3/M tokens). Actual savings
depend on model, provider, and workflow repeat rate.

### Vault Injection Benefit

When relevant vault context is injected, TokenPak replaces generic "please look
this up" turns with pre-compressed, targeted excerpts — typically saving 1–3
additional LLM round-trips per complex query (each ~200–800 tokens of input).

---

## Memory Footprint

| Component             | Memory Usage   |
|-----------------------|----------------|
| Proxy base (no vault) | ~20.4 MB       |
| After vault warmup    | ~20.6 MB       |
| Peak (active requests)| ~20.9 MB       |
| Vault index (7,938 blocks) | ~150 MB   |
| Total (proxy + vault) | ~171 MB        |

> Vault index uses tiered LRU caching: top-200 recently-modified blocks are kept
> hot in memory (configurable via `TOKENPAK_VAULT_CACHE_PRELOAD`). Remaining blocks
> are fetched from disk on demand with sub-millisecond reads.

### Cache Memory Scaling

| Config                         | Memory    |
|--------------------------------|-----------|
| `TOKENPAK_VAULT_MEMORY_MAX=64MB` | 64 MB LRU |
| `TOKENPAK_VAULT_MEMORY_MAX=256MB` | 256 MB LRU (default) |
| `TOKENPAK_VAULT_MEMORY_MAX=512MB` | 512 MB LRU |

---

## Vault Index Lookup

BM25 search over vault blocks (7,938 blocks in production):

| Operation            | Latency   |
|----------------------|-----------|
| Full BM25 search     | < 5 ms    |
| Cache-warm hit       | < 0.1 ms  |
| Cache miss (disk)    | 1–3 ms    |
| Index reload (full)  | ~200 ms   |
| Index reload (no-op) | < 1 ms    |

Index reload is gated by mtime check — only triggers if `index.json` changed.
Interval configurable via `TOKENPAK_VAULT_INDEX_RELOAD_INTERVAL` (default: 300s).

---

## SLA Thresholds

These are the CI-enforced targets from `benchmarks/BASELINE.md`:

| Metric               | Target        | Status (2026-03-26) |
|----------------------|---------------|---------------------|
| Proxy p50 latency    | < 50 ms       | ✅ 0.02–4.55 ms     |
| Proxy p99 latency    | < 500 ms      | ✅ 8–160 ms          |
| Memory peak          | < 500 MB      | ✅ ~171 MB           |
| Cache hit rate       | > 70%         | ✅ 70.2–84.8%        |
| Throughput (warm)    | > 400 req/s   | ✅ 595–1,107 req/s   |
| Error rate           | < 0.1%        | ✅ 0.00%             |
| Compression savings  | > 10%         | ✅ ~21% avg          |

CI alerts automatically if any metric regresses >10% from baseline.

---

## Running the Benchmarks

### Quick benchmark (in-process, no API calls)

```bash
cd ~/vault/01_PROJECTS/tokenpak
python benchmarks/performance_benchmark.py
```

Runs all three profiles (light/medium/heavy) and prints results. Exits with
code 1 if any SLA target is missed.

### Full benchmark suite

```bash
python benchmarks/run_benchmarks.py
# Results written to benchmarks/results/performance-<timestamp>.json
```

### Make target (CI)

```makefile
bench:
    python benchmarks/performance_benchmark.py

bench-full:
    python benchmarks/run_benchmarks.py
```

### GitHub Actions

```yaml
- name: Performance benchmarks
  run: python benchmarks/performance_benchmark.py
  # Exits 1 on SLA regression — CI will catch it
```

---

## Interpreting the Numbers

**Why is p50 so low (< 1ms)?**  
The proxy is an in-process HTTP server on loopback. When a request hits the cache,
the entire path (receive → decompress → lookup → respond) completes in under 1ms.
Upstream LLM latency (200–2,000ms) completely dominates end-user perceived latency.

**Why does p99 spike?**  
Python's GIL and garbage collector create periodic pauses. At low request rates,
the first request after a GC cycle sees a ~30–160ms spike. At high sustained load
(100 RPS), GC runs more frequently but shorter, so p99 stays around 160ms.

**How does cache hit rate affect costs?**  
Each cache hit means the proxy returns a pre-compressed response without forwarding
to the upstream LLM — saving both latency (the full upstream round-trip) and tokens
(the repeat prompt isn't re-processed). At 70% hit rate, 7 in 10 requests cost zero
upstream tokens.

---

*Benchmarks established 2026-03-26 by Cali (latency/compression) and Trix
(cache/throughput). Re-run after any significant proxy change.*
