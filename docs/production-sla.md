---
title: "production-sla"
created: 2026-03-24T19:05:55Z
---
# TokenPak Proxy — Production SLA Targets

_Last updated: 2026-03-24 | Phase 6 Production Hardening_

## Proxy Endpoint SLAs

| Endpoint | p50 target | p95 target | p99 target | Error rate |
|----------|-----------|-----------|-----------|------------|
| `/health` | < 15ms | < 250ms | < 500ms | < 0.1% |
| `/stats` | < 15ms | < 250ms | < 500ms | < 0.1% |
| `/v1/messages` (passthrough) | < 50ms overhead | — | — | < 0.1% |

> **Note:** Targets reflect TrixBot (4GB RAM, Python HTTPServer, 20-worker bounded pool).
> Production deployment with asyncio server (`server_async.py`) should achieve p99 < 20ms.

## Measured Performance (2026-03-24)

Benchmark run on TrixBot, 100 req/sec sustained for 5s:

| Endpoint | p50 | p95 | p99 | Errors |
|----------|-----|-----|-----|--------|
| `/health` | ~4ms | ~100ms | ~250ms | 0% |
| `/stats` | ~4ms | ~100ms | ~250ms | 0% |

## Features Validated (Phase 6)

### /health endpoint
Reports: `status`, `uptime_seconds`, `compression_ratio_avg`, `circuit_breakers`, `index_freshness`, `request_timeout_seconds`

```bash
curl http://localhost:8766/health
curl http://localhost:8766/health?deep=true   # includes memory + disk
```

### Index Freshness Check
`/health` now reports `index_freshness.age_seconds` — stale if > 600s (10 min).
Index auto-reloads every 5min via `VAULT_INDEX_RELOAD_INTERVAL`.

### Graceful Adapter Fallback
Circuit breaker per provider (`tokenpak/agent/proxy/circuit_breaker.py`):
- Opens after 5 failures in 60s window
- HALF_OPEN probe after 60s recovery timeout
- Fail-fast 503 when open (no timeout wasted)

### Request Timeout Enforcement
Set `TOKENPAK_REQUEST_TIMEOUT=<seconds>` to enforce per-request upstream timeout.
Default: `0` (disabled — rely on pool defaults).

```bash
export TOKENPAK_REQUEST_TIMEOUT=30   # 30s hard timeout per request
```

Timeout is passed directly to the connection pool's `stream()` and `request()` calls.

## Load Test Suite

```bash
cd ~/Projects/tokenpak
python -m pytest tests/benchmarks/test_load_100rps.py -v
```

6 tests covering:
- p99 < 500ms at 100 req/sec (5s sustained)
- p50 < 15ms at 100 req/sec
- Zero errors under load
- /stats p99 < 30ms (relative)
- Throughput ≥ 85 req/sec achievable
- JSON validity under concurrent load

## Error Rate Budget

- **Target:** < 0.1% (1 error per 1,000 requests)
- **Measured:** 0% in all load test runs
- **Circuit breaker threshold:** 5 failures / 60s window

## Upgrade Path

For production deployments requiring stricter p99:
1. Switch to `server_async.py` (asyncio-based, removes GIL contention)
2. Gzip-compress vault index blocks (3x smaller, faster load) — see `analysis/index-compression-2026-03.md`
3. Exclude JS/binary chunks from vault index (~7.5MB saved)
