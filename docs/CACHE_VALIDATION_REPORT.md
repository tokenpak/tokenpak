# Cache Hit Rate Validation Report

**Date:** 2026-03-09  
**Analysis Window:** March 5–9, 2026 (Sprint Period)  
**Total Requests Analyzed:** 3,175 (sprint window), 3,924 lifetime

---

## Executive Summary

After P0 cache wiring, P1 poison removal, and P1 telemetry dashboard deployment, an empirical validation of the 10× efficiency goal was performed using live proxy telemetry data.

**Result:** ❌ **Target NOT met — 2.3% overall cache hit rate** (target: ≥60%)

This report documents the findings, root causes, and recommended fixes.

---

## Methodology

### Direct Benchmark (Planned)
- 20 identical requests against live proxy
- Recorded cache hit rates via `/cache-stats` endpoint
- **Status:** Not run — API key unavailable in shell environment

### Substituted Approach (Actual)
- Empirical analysis of `monitor.db` — live proxy telemetry over 5 days
- 3,175 requests during sprint window (Mar 5–9)
- Model-specific breakdown: sonnet, haiku, opus
- Cache hit ratio calculated per request and aggregated

---

## Results by Model

### Overall Aggregate
- **Total requests:** 3,175 (sprint window)
- **Cache hits:** 73
- **Hit rate:** **2.3%**
- **Status:** ❌ FAILED (target: ≥60%)

### Sonnet
- **Requests:** 186
- **Cache hits:** 32
- **Hit rate:** **17.2%**
- **Best day:** March 6 (single day peak)
- **Status:** ⚠️ Poor (even on best day)

### Haiku
- **Requests:** 2,615 (80% of traffic)
- **Cache hits:** 18
- **Hit rate:** **0.7%**
- **Status:** ❌ Critical failure — dominant traffic gets almost no cache

### Opus
- **Requests:** 374
- **Cache hits:** 0
- **Hit rate:** **0%**
- **Status:** ❌ Zero benefit despite large context sizes

---

## Root Cause Analysis

### 1. Cache Control Tied to Vault Injection
- **Issue:** `cache_control` logic only activates when vault context is injected
- **Impact:** Haiku heartbeats (80% of traffic) skip vault, so they skip caching entirely
- **Evidence:** 2,615 haiku requests → 18 hits (0.7%) vs. vault-heavy sonnet → 17.2%
- **Fix:** Decouple cache_control from vault injection; apply to all requests in main pipeline

### 2. Ephemeral TTL (5 minutes)
- **Issue:** Cache entries expire in 5 minutes
- **Impact:** Haiku heartbeat interval is 10 minutes → cache always cold for second request
- **Evidence:** Time-series analysis shows no repeated cache keys within heartbeat cycle
- **Fix:** Increase TTL to 30–60 minutes for telemetry/heartbeat patterns, or implement request batching

### 3. Opus Receives No Cache Benefits
- **Issue:** Large context sizes should benefit most from caching but receive 0% hit rate
- **Evidence:** 374 opus requests, 0 cache hits
- **Likely cause:** Context is too large or too unique (API variations), or cache keys are unstable
- **Fix:** Debug cache key generation for opus; audit whether request normalization is working

### 4. Request-Level Instability
- **Issue:** Identical requests may hash differently due to:
  - Timestamp fields in messages
  - UUID fields in tool calls
  - Schema version headers
  - **Status:** Partially mitigated by P1 poison removal, but not fully resolved
- **Fix:** Audit `FROZEN_TOOL_SCHEMAS` and message normalization; ensure deterministic serialization

---

## Recommended Follow-Up

### Immediate (High Priority)
1. **Decouple cache_control** from vault injection
   - File: `~/tokenpak/tokenpak/pipeline.py`
   - Move cache_control check to main request path (before any conditional logic)
   - Affects: haiku, sonnet, opus equally

2. **Audit cache key generation**
   - File: `~/tokenpak/tokenpak/cache_layer.py`
   - Verify that timestamp/UUID fields are excluded
   - Re-run mypy to confirm type-safe key normalization
   - Test with identical opus requests manually

3. **Increase TTL for heartbeat patterns**
   - Current: 5 minutes
   - Recommended: 30 minutes for telemetry endpoints, 60+ for user requests
   - File: `~/tokenpak/tokenpak/cache_config.py`

### Medium Priority
4. **Batch haiku heartbeats**
   - Combine multiple heartbeat requests into one
   - Would reduce request volume by ~50% and improve cache hit ratios

5. **Add cache warming**
   - Pre-populate common requests (sonnet + default prompts)
   - Reduces cold-start penalty

### Measurement
- **Revalidation:** After fixes, rerun this analysis using monitor.db
- **Target:** Achieve ≥60% hit rate across all models
- **Success metric:** Haiku hits >10%, opus hits >30%, sonnet hits >40%

---

## Conclusion

The 10× efficiency goal is **not yet achieved**. The cache layer is wired and functional (as evidenced by 73 hits across 3,175 requests), but three root causes prevent scaling:

1. Vault injection tie-in skips 80% of traffic
2. TTL too short for heartbeat patterns
3. Opus requests not benefiting from large context caching

**Estimated effort to fix:** 4–6 hours (cache control decoupling + TTL tuning + opus debug)

**Next step:** Implement the immediate fixes above, then rerun validation.

---

**Report generated:** 2026-03-09 18:46 PST  
**Analyst:** Cali (Processor)  
**Status:** Ready for Sue/Kevin review
