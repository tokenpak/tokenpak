# Health Endpoint Audit — 2026-03-09

**Auditor:** Trix  
**Scope:** `GET /health` and `GET /health?deep=true` on the TokenPak proxy  
**Commit ref:** Based on c25910a (version/uptime/requests_total addition)

---

## 1. Endpoint Response Samples

### `GET /health` (basic)
```json
{
    "status": "ok",
    "uptime_seconds": 1,
    "version": "1.0.0",
    "requests_total": 0,
    "requests_errors": 0,
    "compression_ratio_avg": 0.0,
    "is_degraded": false,
    "is_shutting_down": false,
    "in_flight_requests": 0,
    "timestamp": "2026-03-09T12:42:38Z",
    "connection_pool": {
        "http2_enabled": true,
        "active_providers": [],
        "total_requests": 0,
        "reused_connections": 0,
        "new_connections": 0,
        "errors": 0,
        "reuse_rate": 0.0
    },
    "circuit_breakers": {
        "enabled": true,
        "any_open": false,
        "providers": {}
    }
}
```

### `GET /health?deep=true`
```json
{
    "status": "ok",
    "uptime_seconds": 1,
    "version": "1.0.0",
    "requests_total": 0,
    "requests_errors": 0,
    "compression_ratio_avg": 0.0,
    "is_degraded": false,
    "is_shutting_down": false,
    "in_flight_requests": 0,
    "timestamp": "2026-03-09T12:42:39Z",
    "connection_pool": { "...": "..." },
    "circuit_breakers": { "...": "..." },
    "providers": [],
    "memory": { "rss_mb": 87.8 },
    "disk": { "available_gb": 70.49 }
}
```

---

## 2. Bugs Found & Fixed

### Bug 1 — Version hardcoded as `"0.1.0"`
- **File:** `tokenpak/agent/proxy/server.py`, `health()` method
- **Problem:** `"version": "0.1.0"` was a static string; actual package version is `1.0.0`
- **Fix:** Import `tokenpak.__version__` as `_tokenpak_version` and use it dynamically
- **Status:** ✅ Fixed

### Bug 2 — `/health?deep=true` returned 404
- **File:** `tokenpak/agent/proxy/server.py`, `do_GET()` handler
- **Problem:** Route check was `path == "/health"` — query string made it not match
- **Fix:** Extended check to `path.startswith("/health?")`, parse `deep` param, pass to `health(deep=True)`
- **Status:** ✅ Fixed

### Deep Health Fields Added
- `providers` — list of active providers with circuit-breaker state
- `memory` — RSS memory usage in MB (via `psutil`, graceful fallback)
- `disk` — available disk in GB (via `shutil.disk_usage`)

---

## 3. Test Results

### Health-specific tests (`tests/test_proxy_health.py`)
```
18 passed in 2.38s
```
All 18 tests passing, including:
- HTTP 200, required fields, field types
- `requests_total` increment
- `compression_ratio_avg` rolling window
- Response time < 50ms
- No auth required
- Content-Type: application/json

### Full test suite
```
3128 passed, 81 skipped, 7 pre-existing failures
```

The 7 failures are all `ModuleNotFoundError` for optional integrations (`crewai`, `autogen_tokenpak`) — pre-existing, not caused by this change. All 83 tokenpak-local tests pass.

---

## 4. Verification Summary

| Check | Result |
|-------|--------|
| `status` field present | ✅ |
| `version` matches `__init__.py` (1.0.0) | ✅ Fixed |
| `uptime_seconds` positive int | ✅ |
| `requests_total` non-negative int | ✅ |
| `requests_total` increments (unit test) | ✅ |
| `deep=true` returns `providers`, `memory`, `disk` | ✅ Fixed |
| All health unit tests pass (18/18) | ✅ |
| Full suite stable (no regressions) | ✅ |
