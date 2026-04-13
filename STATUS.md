# TokenPak — Service Status

> **Manual status page (v1).** Updated by maintainers on incidents and service changes.
> Real-time monitoring integration is a future milestone.

## Current Status

| Component | Status | Notes |
|-----------|--------|-------|
| **Proxy** (`localhost:8766`) | 🟢 Operational | Stable since 2026-03-14 fix |
| **Portal** (`app.tokenpak.ai`) | 🟢 Operational | |
| **Metrics Ingest** (`metrics.tokenpak.ai`) | 🟢 Operational | |

---

## Incidents

### 2026-03-14 — Swap Exhaustion (Proxy)

**Severity:** High
**Duration:** Ongoing until patched (detected after ~14 h uptime)
**Status:** Resolved

**Summary:**
The TokenPak proxy process accumulated ~3.9 GB of swap after approximately 14 hours of continuous operation. The root cause was that the `SemanticCache` object was being instantiated on every request rather than once at module load time, producing a per-request memory leak that compounded under sustained load.

**Impact:**
- Proxy memory consumption grew unbounded on long-running deployments
- Systems with limited swap space experienced OOM conditions or severe latency degradation
- No data loss or credential exposure

**Resolution:**
Converted `SemanticCache` instantiation to a module-level singleton (`_SEM_CACHE_SINGLETON`) so the object is created at most once per process lifetime. See `proxy.py` for implementation.

**Prevention:**
- Added singleton pattern for all cache objects instantiated in the request path
- Memory profiling added to the performance benchmark suite

---

## Maintenance Windows

No scheduled maintenance at this time.

---

## Contact

For urgent service issues: open a GitHub issue or email **support@tokenpak.dev**.
For security vulnerabilities: see [SECURITY.md](SECURITY.md).
