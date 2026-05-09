# TokenPak Cache Telemetry Dashboard

> **Endpoint:** `GET http://localhost:8766/cache-stats`
> **Updated:** Live — every proxy request updates the metrics.

---

## Overview

The cache dashboard provides real-time visibility into how well TokenPak's
prompt-cache layer is working. The key metric is **cache hit rate**: the
fraction of requests where the LLM served at least some tokens from its
prompt cache rather than re-processing them.

**Target hit rate:** ≥ 60% (after P0 wiring + P1 poison removal).

---

## Reading the Dashboard

```bash
curl -s http://localhost:8766/cache-stats | python3 -m json.tool
```

Example output:

```json
{
 "total_requests": 42,
 "cache_hits": 31,
 "cache_misses": 11,
 "hit_rate": 0.7381,
 "miss_rate": 0.2619,
 "hit_rate_pct": 73.8,
 "avg_cache_ratio": 0.8851,
 "avg_cache_ratio_pct": 88.5,
 "total_cache_read_tokens": 415000,
 "total_cache_creation_tokens": 15200,
 "total_input_tokens": 469000,
 "total_output_tokens": 120300,
 "estimated_cost_saved_tokens": 373500.0,
 "miss_reasons": {
 "timestamp": 7,
 "uuid": 2,
 "unknown": 2
 },
 "recent_requests": [...]
}
```

---

## Metrics Reference

| Field | Description |
|-------|-------------|
| `total_requests` | Total proxy requests recorded this session |
| `cache_hits` | Requests where LLM served ≥ 1 token from cache |
| `cache_misses` | Requests with zero cache-read tokens |
| `hit_rate` | `cache_hits / total_requests` (0.0–1.0) |
| `hit_rate_pct` | Same, expressed as a percentage |
| `avg_cache_ratio` | Average fraction of *input* tokens served from cache |
| `avg_cache_ratio_pct` | Same, as percentage |
| `total_cache_read_tokens` | Cumulative cache-read tokens (cheap at ~10% cost) |
| `total_cache_creation_tokens` | Tokens written to cache (normal cost) |
| `total_input_tokens` | Total input tokens sent to LLM |
| `estimated_cost_saved_tokens` | `cache_read_tokens × 0.90` — equivalent fresh tokens saved |
| `miss_reasons` | Histogram of diagnosed cache-miss causes |
| `recent_requests` | Last 10 requests with per-request detail |

---

## Cache Miss Reasons

| Reason | Cause | Fix |
|--------|-------|-----|
| `timestamp` | ISO timestamp detected in stable prefix | Move timestamps to logging only |
| `uuid` | UUID / request_id in prompt text | Move to headers / logging |
| `tool_schema` | Tool schemas re-rendered each request | Use `FROZEN_TOOL_SCHEMAS` (P1 task) |
| `retrieval` | Non-deterministic retrieval order | Sort/deduplicate retrieved chunks |
| `unknown` | Miss with no diagnosed cause | Needs deeper audit |

---

## Interpreting Results

### 🟢 Healthy (> 60% hit rate)

The cache is working. Monitor `avg_cache_ratio` — this should be 85–95%
on well-structured prompts.

### 🟡 Degraded (30–60% hit rate)

Some poison remains. Check `miss_reasons` histogram to identify the largest
category, then apply the fix from the table above.

### 🔴 Broken (< 30% hit rate)

Cache is not being utilised. Likely causes:
1. `apply_stable_cache_control` hook not wired (re-run P0 task)
2. Tool schemas not frozen (re-run P1 poison task)
3. Timestamps injected into every system prompt

---

## Cost Savings Estimate

`estimated_cost_saved_tokens` gives the number of *equivalent fresh input
tokens* saved. Multiply by your per-token price to get dollar savings:

```
savings_usd ≈ estimated_cost_saved_tokens × (input_price_per_token × 0.90)
```

For Claude claude-sonnet-4-6 (`claude-sonnet-4-6`) at $3.00/MTok input:

```
savings_usd ≈ estimated_cost_saved_tokens × (3.00 / 1_000_000) × 0.90
```

---

## Recent Requests (per-request detail)

Each entry in `recent_requests`:

```json
{
 "request_id": "a1b2c3d4",
 "stable_prefix_tokens": 15000,
 "stable_cached": true,
 "cache_hit": true,
 "cache_hit_ratio": 0.9211,
 "cache_miss_reason": null,
 "volatile_tail_tokens": 200,
 "total_input_tokens": 15200,
 "cache_read_tokens": 14001,
 "cache_creation_tokens": 0,
 "output_tokens": 512,
 "cost_saved": 12600.9,
 "timestamp": 1741555574.12
}
```

`cache_hit_ratio = cache_read_tokens / total_input_tokens`

---

## Integration with Prometheus

TokenPak's telemetry server exposes Prometheus metrics at `/metrics`
(if the telemetry server is running on port 8769). The cache metrics
are also included there under the `tokenpak_cache_*` namespace.

---

## Implementation Notes

- Metrics are **in-memory only** — they reset when the proxy restarts.
- The last 100 per-request snapshots are kept in the rolling window.
- Miss-reason diagnosis is **heuristic** (best-effort pattern matching on
 the request body); it may misclassify edge cases.
- `estimated_cost_saved_tokens` uses a fixed 90% saving factor (cache reads
 cost 10% of fresh input price on Anthropic's API).

---

