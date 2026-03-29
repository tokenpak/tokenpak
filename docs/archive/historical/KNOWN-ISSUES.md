---
title: "TokenPak Known Issues & Status"
created: 2026-03-27T13:50Z
updated: 2026-03-27T13:50Z
maintainer: Cali
status: current
---

# TokenPak Known Issues & Status

**Last Updated:** 2026-03-27  
**Scope:** TokenPak proxy and SDK (v0.5.0+)  

---

## Active Issues (Requires Fix)

### 1. Load Test Scaling Analysis — API Rate Limiting

**Issue:** Cannot measure pure proxy throughput with real API calls due to upstream rate limiting.

**Status:** 🟡 BLOCKED  
**Priority:** P2  
**Introduced:** 2026-03-27  
**Assigned to:** Cali  
**Related Task:** `p2-tokenpak-load-test-scaling-analysis`  

**Description:**  
The load test framework is complete and working, but testing with real API calls hits rate limits on the test API key after the first few requests. This prevents accurate measurement of proxy scaling characteristics.

**Symptoms:**
- Initial request: 200 OK (~3000-4000ms latency due to LLM)
- Follow-up requests: 429 Too Many Requests
- Load test results: 100% error rate after first request

**Workarounds:**
1. Use API key with elevated rate limits
2. Deploy mock response server for synthetic testing
3. Use separate testing account with higher tier

**Regression Test:**  
See `tests/test_regressions.py::TestKnownOpenIssues::test_load_test_with_real_api_calls`

---

## Resolved Issues (Regression Tested)

### RESOLVED: BrokenPipeError Handling

**Status:** ✅ FIXED  
**Fix Date:** 2026-03-25  
**Commit:** 15fd77cb2  
**Related Task:** `p2-tokenpak-proxy-brokenipe-fix`  

**Issue:**  
Proxy crashed with BrokenPipeError when client connection dropped during response transmission.

**Fix:**  
Proper exception handling in response streaming logic.

**Regression Test:**  
`tests/test_regressions.py::TestBrokenPipeHandling`

---

### RESOLVED: Pytest Collection Errors

**Status:** ✅ FIXED  
**Fix Date:** 2026-03-24  
**Commit:** 1d5b51e79  
**Related Task:** `TPK-PYTEST-COLLECT-FIX`  

**Issue:**  
Pytest couldn't collect tests due to module rename (`proxy_v4` → `proxy`).

**Fix:**  
Updated import paths in test files.

**Evidence:**  
`pytest --collect-only` now reports 8848 tests, 0 errors.

**Regression Test:**  
`tests/test_regressions.py::TestPytestCollectionIssues`

---

### RESOLVED: Coverage Configuration

**Status:** ✅ FIXED  
**Fix Date:** 2026-03-25  
**Commit:** f486726d4  
**Related Task:** `TPK-COV-FIX`  

**Issue:**  
Coverage report omit list was too aggressive, excluding 22 critical files.

**Fix:**  
Reduced omit list to actual non-critical paths (build artifacts, cache).

**Evidence:**  
Coverage: 1% → 17% (accurate picture of actual gaps).

**Regression Test:**  
`tests/test_regressions.py::TestCoverageIssues`

---

### RESOLVED: Proxy Crash Loop (WatchdogSec)

**Status:** ✅ FIXED  
**Fix Date:** 2026-03-20  
**Commit:** e55144289  
**Related Task:** Heartbeat-triggered incident fix  

**Issue:**  
Proxy crashed due to WatchdogSec=30 timeout during startup.

**Fix:**  
- Removed WatchdogSec timer
- Fixed `ensure-port-free.sh` to use `fuser` instead of `lsof`

**Regression Test:**  
`tests/test_regressions.py::TestProxyCrashHandling`

---

### RESOLVED: Pool Manager + HTTP Methods

**Status:** ✅ FIXED  
**Fix Date:** 2026-03-22  
**Commit:** 06c5e5d32  
**Related Task:** Consolidation task  

**Issue:**  
Proxy didn't support HEAD, OPTIONS methods; connection pooling had issues.

**Fix:**  
Consolidated proxy.py with proper pool manager and HTTP method support.

**Features Added:**
- Full HTTP method support (GET, POST, HEAD, OPTIONS, etc.)
- Connection pooling with reuse
- WebSocket reuse_address=True

**Regression Test:**  
`tests/test_regressions.py::TestProxyPoolManagerIssues`

---

### RESOLVED: Error Code Forwarding

**Status:** ✅ FIXED  
**Fix Date:** 2026-03-26  
**Commit:** 7c1b4d6b3  
**Related Task:** `p2-tokenpak-error-handling-tests`  

**Issue:**  
Proxy wasn't properly handling/forwarding upstream error codes (400, 401, 429, 500, 502, 503).

**Fix:**  
Added comprehensive error handling in proxy request/response pipeline.

**Test Coverage:**  
48 comprehensive error handling tests, all passing.

**Regression Test:**  
`tests/test_regressions.py::TestErrorCodeHandling`

---

## Monitoring & Prevention

### Regression Test Suite

All issues above are documented in `tests/test_regressions.py`.

**Run regression tests:**
```bash
pytest tests/test_regressions.py -v
```

**CI Integration:**  
Regression tests should run on every commit to prevent reintroduction of fixed issues.

### Issue Tracking Workflow

1. **Bug Found** → Create issue ticket with repro steps
2. **Fix Implemented** → Add regression test
3. **QA Approved** → Move issue from "Active" to "Resolved" in this document
4. **Regression Test** → Ensures fix remains in place

---

## Statistics

| Status | Count |
|--------|-------|
| Active Issues | 1 |
| Resolved (Regression Tested) | 7 |
| Total Known Issues | 8 |

---

## Future Work

- [ ] Complete load test scaling analysis (blocked on API rate limits)
- [ ] Add regression tests for any issues found during OSS beta
- [ ] Set up automated regression test CI/CD
- [ ] Document post-launch issues

---

*Document maintained by Cali. Last updated 2026-03-27T13:50Z*
