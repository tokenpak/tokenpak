# Stage 1 → Stage 2 Approval Criteria

**Decision Date:** 2026-03-18 EOD
**Decision Maker:** Kevin Yang
**Prepared By:** Sue (QA) + Trix (baseline data)

## Go/No-Go Criteria (ALL must pass)

### 1. Uptime ≥ 99%
- **Measure:** Total uptime hours / 168h (7 days)
- **Pass:** ≥ 166h uptime
- **Fail:** Any unplanned restart or crash

### 2. Latency Stable
- **Measure:** p50 and p95 latency across 7-day window
- **Pass:** p50 < 50ms, p95 < 200ms, variance ≤ 5% day-over-day
- **Fail:** Any day with p50 > 60ms or p95 > 250ms

### 3. Error Rate < 1%
- **Measure:** Failed requests / total requests per provider
- **Pass:** < 1% error rate for each provider (Anthropic, Google, OpenAI)
- **Fail:** Any provider > 1% or new error types appearing

### 4. Throughput Consistent
- **Measure:** Requests/minute average per day
- **Pass:** ±10% variance from Day 1 baseline
- **Fail:** Sustained throughput drop > 15%

### 5. Memory Stable
- **Measure:** RSS memory of proxy process over 7 days
- **Pass:** < 50MB growth over full period
- **Fail:** Monotonic memory increase > 50MB (leak indicator)

## Stage 2 Plan (Upon Approval)

Enable Tier 1 modules one-by-one (2026-03-19 to 2026-03-25):

| Day | Module | Toggle |
|-----|--------|--------|
| Day 1 | Semantic Cache | `TOKENPAK_SEMANTIC_CACHE=1` |
| Day 2 | Prefix Registry | `TOKENPAK_PREFIX_REGISTRY=1` |
| Day 3 | Compression Dict | `TOKENPAK_COMPRESSION_DICT=1` |
| Day 4 | Trace (optional) | `TOKENPAK_TRACE=1` |
| Day 5-7 | Monitor all Tier 1 | All 4 enabled |

## Rollback Trigger

If ANY go/no-go criterion fails during Stage 2:
1. Disable the most recently enabled toggle
2. Monitor 24h
3. If stable → re-enable with investigation
4. If unstable → rollback to Stage 1 baseline

See `phase3-rollback-procedure.md` for detailed steps.
