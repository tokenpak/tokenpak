# TokenPak Proxy — Performance Baseline

## Hardware: CaliBOT

| Spec | Value |
|------|-------|
| Host | CaliBOT (`cali@calibot`) |
| RAM | 4 GB |
| CPU | 4 cores |
| GPU | None |
| OS | Linux 6.17.0-19-generic |

## Baseline Run — 2026-03-26

Measured via `tests/benchmarks/test_load_100rps.py` against `tokenpak.agent.proxy.server.ProxyServer`.

### 100 RPS Sustained Load (`/health`, 5 seconds, 20 concurrent workers)

| Metric | Value |
|--------|-------|
| Throughput | ~98.3 RPS |
| Total Requests | 493 |
| Error Rate | 0.00% |
| p50 latency | 4.55 ms |
| p95 latency | 5.51 ms |
| p99 latency | 159.74 ms |

### Sequential /health (200 requests, single-threaded)

| Metric | Value |
|--------|-------|
| p50 latency | < 5 ms |
| p95 latency | < 15 ms |
| p99 latency | < 50 ms |

### SLA Thresholds (CaliBOT hardware)

| Metric | Threshold | Notes |
|--------|-----------|-------|
| p50 latency | < 5 ms | Warm cached /health |
| p95 latency | < 15 ms | |
| p99 latency | < 500 ms | GIL/GC tail spikes expected on 4-core constrained hardware under sustained 100 RPS; tighten if hardware improves |
| Error rate | < 0.1% | |
| Throughput | ≥ 400 req / 5s | ~80 RPS minimum |

## Notes

- p99 at 159ms under 100 RPS burst is expected on 4GB/4-core hardware due to Python GIL and GC pressure
- p50 and p95 remain well within budget (4.55ms / 5.51ms)
- These numbers reflect the `/health` endpoint (in-memory, no upstream calls)
- Proxy endpoint latency will be higher due to upstream API call overhead
