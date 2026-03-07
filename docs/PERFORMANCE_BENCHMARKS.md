# TokenPak Performance Benchmarks

**Version:** v1.0 RC1  
**Date:** 2026-03-06  
**Environment:** TrixBot (4GB RAM, Python 3.12)

## Summary

| Metric | Value | Target | Status |
|--------|-------|--------|--------|
| Compression Rate | 27.4% | >20% | ✅ |
| Test Pass Rate | 97.7% | >95% | ✅ |
| Startup Time | <2s | <5s | ✅ |
| Memory (idle) | ~150MB | <500MB | ✅ |

## Test Suite Results

```
2176 passed, 52 skipped, 0 failed
Duration: 93.13s
```

### Test Coverage by Module

| Module | Tests | Status |
|--------|-------|--------|
| Core compression | 180+ | ✅ |
| Vault indexing | 48 | ✅ |
| CANON dedup | 35 | ✅ |
| Telemetry | 64 | ✅ |
| CLI commands | 120+ | ✅ |
| Phase 5 (ingest/query) | 85 | ✅ |
| Workflow engine | 63 | ✅ |
| Cost/budget tracking | 50 | ✅ |

## Production Metrics (7-day rolling)

From `~/.openclaw/workspace/.ocp/monitor.db`:

| Metric | Value |
|--------|-------|
| Total Requests | 4,032 |
| Cache Hit Rate | 71.8% |
| Tokens Processed | 43.1M |
| Tokens Saved | 606K (this session) |
| Cost Saved | $11.74 (this session) |

## Compression Performance

### By Compilation Mode

| Mode | Compression | Use Case |
|------|-------------|----------|
| `strict` | 5-15% | Code-heavy prompts |
| `hybrid` | 20-35% | Mixed content (default) |
| `aggressive` | 40-60% | Narrative/docs |

### By Content Type

| Content | Compression | Notes |
|---------|-------------|-------|
| System prompts | 10-20% | Protected by default |
| Tool schemas | 0% (frozen) | ToolSchemaRegistry handles |
| User messages | 15-25% | Style-contract aware |
| Vault injection | 30-40% | BM25 retrieval |

## Latency

Average request latency (from proxy logs):

| Percentile | Latency |
|------------|---------|
| P50 | ~500ms |
| P95 | ~1200ms |
| P99 | ~2500ms |

Note: Latency includes upstream API time. TokenPak overhead is <50ms.

## Memory Usage

| State | RAM |
|-------|-----|
| Idle proxy | ~150MB |
| Active (high load) | ~300MB |
| Peak (large vault) | ~450MB |

## Throughput

| Scenario | Requests/sec |
|----------|--------------|
| Single worker | 2-3 |
| 4 workers | 8-10 |
| With Redis cache | 15-20 |

## Edge Cases Tested

✅ Empty input handling  
✅ Large inputs (>100K tokens) — chunked processing  
✅ Unicode/emoji — preserved correctly  
✅ Malformed JSON — graceful error  
✅ Network timeouts — retry with backoff  
✅ Rate limiting — automatic failover  
✅ Concurrent requests — thread-safe  

## Benchmark Methodology

1. **Unit tests:** pytest with mocked APIs
2. **Integration tests:** Real API calls (test accounts)
3. **Load tests:** locust with 10 concurrent users
4. **Production metrics:** SQLite telemetry from live proxy

## Known Limitations

1. **Cache telemetry gap:** cache_read_tokens showing 0 on some days (logging issue)
2. **Haiku skip:** Vault injection skipped for haiku models (by design)
3. **Large tool schemas:** >50 tools may cause startup delay

## Recommendations

1. Use `hybrid` mode for general workloads
2. Enable Redis for multi-worker deployments
3. Set `TOKENPAK_LOG_LEVEL=WARNING` in production
4. Monitor `/health` endpoint for drift detection
