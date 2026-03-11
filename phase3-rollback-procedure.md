# Phase 3 Rollback Procedure

**Emergency response:** Revert to last known-good state in < 5 minutes.

## Quick Rollback (< 2 minutes)

### Option A: Disable Specific Module
```bash
# If a single module causes issues, disable just that toggle:
export TOKENPAK_<MODULE_NAME>=0
# Restart proxy
pkill -f proxy_v4.py && sleep 2 && python3 proxy_v4.py &
```

### Option B: Disable ALL Modules
```bash
# Unset all toggles (revert to baseline)
unset TOKENPAK_SEMANTIC_CACHE TOKENPAK_PREFIX_REGISTRY TOKENPAK_COMPRESSION_DICT
unset TOKENPAK_TRACE TOKENPAK_BUDGET_CONTROLLER TOKENPAK_REQUEST_LOGGER
unset TOKENPAK_ERROR_NORMALIZER TOKENPAK_SALIENCE_ROUTER TOKENPAK_CACHE_REGISTRY
unset TOKENPAK_RETRIEVAL_WATCHDOG TOKENPAK_FAILURE_MEMORY TOKENPAK_FIDELITY_TIERS
unset TOKENPAK_PRECONDITION_GATES TOKENPAK_QUERY_REWRITER TOKENPAK_SESSION_CAPSULES
unset TOKENPAK_STABILITY_SCORER

pkill -f proxy_v4.py && sleep 2 && python3 proxy_v4.py &
```

### Option C: Full Code Rollback
```bash
cd ~/tokenpak  # or ~/Projects/tokenpak on Trix

# Phase 2 (Tier 1 + Tier 2, no Phase 3):
cp proxy_v4-checkpoint-pre-phase3.py proxy_v4.py

# Phase 1 only (Tier 1 only):
cp proxy_v4-checkpoint-pre-tier2.py proxy_v4.py

# Pre-consolidation (original production):
cp ~/tokenpak-proxy_v4-PRODUCTION-20260311.py proxy_v4.py

pkill -f proxy_v4.py && sleep 2 && python3 proxy_v4.py &
```

## Checkpoint Inventory

| Checkpoint | Lines | State |
|------------|-------|-------|
| `proxy_v4-checkpoint-phase3-complete.py` | 3591 | Phase 3 complete |
| `proxy_v4-checkpoint-pre-phase3.py` | 3521 | Phase 2 end |
| `proxy_v4-checkpoint-phase2-complete.py` | 3355 | Phase 2 mid |
| `proxy_v4-checkpoint-post-tier2a.py` | 3457 | Tier 2A done |
| `proxy_v4-checkpoint-pre-tier2.py` | 3361 | Tier 1 end |
| `proxy_v4-checkpoint-phase1-complete.py` | 3355 | Phase 1 end |
| `proxy_v4-checkpoint-phase1a.py` | 3311 | Phase 1 start |
| `tokenpak-proxy_v4-PRODUCTION-20260311.py` | ~3300 | Pre-consolidation |

## Post-Rollback Verification

```bash
# 1. Proxy running?
curl -s http://localhost:8766/health

# 2. Tests pass?
python3 -m pytest tests/ -q --tb=line -x 2>&1 | tail -5

# 3. No module errors in logs?
grep -c "error" /tmp/tokenpak-proxy.log | tail -5
```

## Escalation

If rollback fails:
1. Kill proxy: `pkill -9 -f proxy_v4.py`
2. Restore production backup: `cp ~/tokenpak-proxy_v4-PRODUCTION-20260311.py proxy_v4.py`
3. Restart: `python3 proxy_v4.py &`
4. Alert Kevin via Telegram
