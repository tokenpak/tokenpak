# TokenPak Proxy — Release Validation Targets

_Last updated: 2026-07-23_

These are governed release-validation thresholds, not a universal performance
claim. An absolute pass requires the reference profile and immutable receipt
defined by the TokenPak benchmarking contract.

## Warmed Endpoint Targets

| Endpoint | p50 target | p95 target | p99 target | Error rate |
|----------|-----------|-----------|-----------|------------|
| `/health` | < 15ms | < 250ms | < 500ms | < 0.1% |
| `/stats` | < 15ms | < 250ms | < 500ms | < 0.1% |
| `/v1/messages` (passthrough) | < 50ms overhead | — | — | < 0.1% |

For `/health`, the governed warmed test uses an explicit readiness barrier,
20-request warm-up, and then exactly 500 open-loop requests at 100 requests/s
with 20 workers. Cold listener readiness and startup admission are recorded in
separate datasets and never merged into the warmed percentile vector. Shared
or nonmatching CI hosts produce informational evidence only; they do not widen
the 500-ms p99 threshold.

## Features Validated (Phase 6)

### /health endpoint
The basic response reports: `status`, `uptime_seconds`, `version`,
`requests_total`, `requests_errors`, `compression_ratio_avg`, `is_degraded`,
`is_shutting_down`, `in_flight_requests`, `memory_guard`, `admission`,
`agent_concurrency`, `timestamp`, `connection_pool`, and `circuit_breakers`.

```bash
curl http://localhost:8766/health
curl 'http://localhost:8766/health?deep=true' # additive providers, memory, disk
```

The basic response is uncached. Deep mode returns JSON even when the optional
process-memory dependency is unavailable and distinguishes unavailable from a
measured zero. The snapshot is assembled from several bounded runtime reads;
it is operationally current but is not a transactional cross-field snapshot.

### Graceful Adapter Fallback
Circuit breaker per provider (`tokenpak/agent/proxy/circuit_breaker.py`):
- Opens after 5 failures in 60s window
- HALF_OPEN probe after 60s recovery timeout
- Fail-fast 503 when open (no timeout wasted)

### Request Timeout Enforcement
Set `TOKENPAK_REQUEST_TIMEOUT=<seconds>` to enforce per-request upstream timeout.
Default: `0` (disabled — rely on pool defaults).

```bash
export TOKENPAK_REQUEST_TIMEOUT=30 # 30s hard timeout per request
```

Timeout is passed directly to the connection pool's `stream()` and `request()` calls.

## Load Test Suite

```bash
cd ~/Projects/tokenpak
python -m pytest tests/benchmarks/test_load_100rps.py -v
```

The governed runner retains every latency observation and its complete machine
receipt. It fails closed for missing or duplicate samples, generator
saturation, runner/artifact drift, missing telemetry, listener drops or
overflows, request errors, or a nonmatching reference profile. The regular
test suite also retains functional `/health` and `/stats` checks; those tests
do not substitute for the release receipt.

## Error Rate Budget

- **Target:** < 0.1% (1 error per 1,000 requests)
- **Circuit breaker threshold:** 5 failures / 60s window

## Upgrade Path

If the governed warmed target fails, preserve the raw vectors and host/process
telemetry, classify the cause, and repair the implementation or harness. Do
not raise the threshold or reinterpret a passing retry without separately
governed evidence.
