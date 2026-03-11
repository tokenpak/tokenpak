# Stage 1 Baseline Deployment Guide

**Phase:** Stage 1 — Baseline Monitoring (Week 1)
**Timeline:** 2026-03-11 to 2026-03-18
**Commit:** `83c90f4` (Phase 3 complete, all 16 modules wired)

## Pre-Deployment Checklist

- [ ] All 3 machines at commit `83c90f4`
- [ ] All 16 toggles set to OFF (default)
- [ ] Proxy port: 8766

## Deployment Steps

### Per Machine (Sue / Trix / Cali)

```bash
cd ~/tokenpak  # or ~/Projects/tokenpak on Trix

# 1. Verify commit
git log --oneline -1
# Expected: 83c90f4 feat: wire Phase 3 modules ...

# 2. Verify all toggles OFF
env | grep TOKENPAK_ | sort
# Expected: no TOKENPAK_ vars set (all default OFF)

# 3. Start proxy
python3 proxy_v4.py &

# 4. Verify startup
curl -s http://localhost:8766/health | python3 -m json.tool
# Expected: {"status": "ok", ...}

# 5. Verify no Phase 3 module activity
grep -c "precondition_gates\|query_rewriter\|session_capsules\|stability_scorer" /tmp/tokenpak-proxy.log 2>/dev/null
# Expected: 0 (no module activity when toggles OFF)
```

## Toggle Reference (ALL OFF for Stage 1)

```bash
TOKENPAK_SEMANTIC_CACHE=0
TOKENPAK_PREFIX_REGISTRY=0
TOKENPAK_COMPRESSION_DICT=0
TOKENPAK_TRACE=0
TOKENPAK_BUDGET_CONTROLLER=0
TOKENPAK_REQUEST_LOGGER=0
TOKENPAK_ERROR_NORMALIZER=0
TOKENPAK_SALIENCE_ROUTER=0
TOKENPAK_CACHE_REGISTRY=0
TOKENPAK_RETRIEVAL_WATCHDOG=0
TOKENPAK_FAILURE_MEMORY=0
TOKENPAK_FIDELITY_TIERS=0
TOKENPAK_PRECONDITION_GATES=0
TOKENPAK_QUERY_REWRITER=0
TOKENPAK_SESSION_CAPSULES=0
TOKENPAK_STABILITY_SCORER=0
```

## Monitoring During Week 1

Run daily at 9 AM PDT:
```bash
python3 baseline-week1-collection-2026-03-11-to-2026-03-18.py
```

Captures: request throughput, latency p50/p95, error rates, session counts.

## Success Criteria

| Metric | Target |
|--------|--------|
| Uptime | 99%+ (no unexpected restarts) |
| Latency p50 | < 50ms |
| Latency p95 | < 200ms |
| Error rate | < 1% per provider |
| Memory growth | < 50MB over 7 days |

## Next: Stage 2

After Kevin approves baseline health on 2026-03-18, proceed to Stage 2 (enable Tier 1 modules).
