---
title: "AsyncIO Migration Spike Report"
date: 2026-03-26
author: Cali
status: draft
commitment: Phase 3 / Post-Launch
related_tasks: [TPK-ASYNCIO-SPIKE, proposal-tokenpak-proxy-hanging-fix-2026-03-26]
---

# AsyncIO Migration Spike Report

**Date:** 2026-03-26  
**Author:** Cali  
**Goal:** Scope effort to migrate `proxy.py` from `http.server` + threading to `asyncio` + `aiohttp`

---

## Current Threading Surface

### Code Metrics
- **Total lines:** 5,554
- **threading.Lock instances:** 14
- **threading.Thread instances:** 6  
- **HTTP server framework:** `http.server.BaseHTTPRequestHandler` (1 reference)

### Global Locks (State Protection)

| Lock Name | Purpose | Scope |
|-----------|---------|-------|
| `_KEY_COOLDOWN_LOCK` | API key rotation cooldown | Global |
| `_KEY_RR_LOCK` | Round-robin key selection | Global |
| `_ollama_circuit_lock` | Ollama circuit breaker | Global |
| `_provider_circuit_lock` | Provider health tracking | Global |
| `_rate_bucket_lock` | Token bucket rate limiter | Global |
| `_ROUTER_LOCK` | Route engine state | Global |
| `_VALIDATION_GATE_LOCK` | Request validation gates | Global |
| `_ROUTE_ENGINE_LOCK` | Router state | Global |
| `_PRECOND_GATES_LOCK` | Precondition check gates | Global |
| `_BUDGET_CTRL_LOCK` | Budget control state | Global |
| `_DB_LOCK` | SQLite writes | Global |
| `_active_request_lock` | Active request tracking | Global |
| `_LAST_REQUEST_LOCK` | Last request timestamp | Global |
| `_ws_active_connections_lock` | WebSocket connections | Global |

### Thread Instances

1. **Health check thread** (`_ollama_health_thread`)  
   - Monitors Ollama provider health
   - Runs daemon loop with polling
   
2. **Database writer thread** (`_DB_WRITER_THREAD`)  
   - Handles async SQLite writes
   - Queue-based buffering
   
3. **HTTP handler threads** (spawned per request)  
   - `BaseHTTPRequestHandler` creates a thread per connection
   - ~line 5193: `threading.Thread(target=self._handle, args=(request, client_address))`
   
4. **WebSocket server thread** (line 5373+)  
   - Separate WebSocket listener
   - Spawns threads for each connection
   
5. **Sync loop thread** (line 5469)  
   - Background sync operations
   
6. **Shutdown thread** (line 5508)  
   - Graceful shutdown handler

---

## Blocking Patterns Requiring Refactor

### Pattern: `time.sleep()`
- **Count:** 4 instances
- **Refactor:** Replace with `await asyncio.sleep()`
- **Effort:** Trivial (1 line per instance)

### Pattern: SQLite Blocking I/O
- **Count:** 8 sqlite3 references
- **Current mitigation:** Queue-based writer thread
- **Refactor:** Migrate to `aiosqlite` or keep queue but with asyncio locks
- **Effort:** Moderate (~50 LOC)
- **Risk:** HIGH — SQLite schema changes unlikely, but async wrapper adds complexity

### Pattern: HTTP Requests (urllib/httplib)
- **Count:** 14 references
- **Refactor:** Replace with `aiohttp.ClientSession`
- **Effort:** Moderate (~100 LOC)
- **Risk:** LOW — Direct substitution, test coverage exists

### Pattern: Thread Spawning
- **Count:** 6 `threading.Thread()` calls
- **Refactor:** Convert to async tasks or background coroutines
- **Effort:** High (~200 LOC) — need to understand each thread's purpose
- **Risk:** HIGH — Timing-dependent behavior may change

### Pattern: Lock Contention
- **Count:** 14 global locks protecting shared state
- **Refactor:** Convert to `asyncio.Lock` primitives
- **Effort:** Moderate (~50 LOC) — mechanical change, but requires understanding each lock's hold time
- **Risk:** MEDIUM — Must verify no blocking operations inside lock scopes

---

## Framework Migration: `http.server` → `aiohttp`

### Current Setup
```python
from http.server import BaseHTTPRequestHandler, HTTPServer

class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        ...
    def do_POST(self):
        ...
```

### AsyncIO Equivalent
```python
from aiohttp import web

async def handle_request(request):
    ...
    return web.Response(...)

app = web.Application()
app.router.add_post('/proxy', handle_request)
```

### Migration Effort: **HIGH**
- Signature changes from method-dispatch to coroutine handlers
- `self.path`, `self.headers` → `request.path`, `request.headers`
- `self.wfile.write()` → `return web.StreamResponse()`
- Streaming responses require `aiohttp.web.StreamResponse` with `write()` calls

**Estimated LOC affected:** ~1,000+ (routing, headers, response writing)

---

## Risk Assessment

### HIGH RISK Areas
1. **Lock hold times** — If any lock is held during I/O, asyncio will hang
2. **Streaming responses** — Current code uses `self.wfile.write()` during long operations
3. **WebSocket integration** — Mixed threading + async is a known pain point
4. **Request/response lifecycle** — `BaseHTTPRequestHandler` is stateful per-connection thread; `aiohttp` handlers are stateless coroutines

### MEDIUM RISK Areas
1. **SQLite serialization** — `aiosqlite` adds latency vs. threaded queue
2. **Provider health checks** — Must not block event loop
3. **Rate limiting** — Token bucket logic may have edge cases in async

### LOW RISK Areas
1. `time.sleep()` → `asyncio.sleep()`
2. HTTP request patterns (urllib → aiohttp)

---

## Recommended Approach

### Option A: Full Rewrite (4-5 days)
**Pros:**
- Clean asyncio throughout
- Better performance potential
- No hybrid threading/async complexity

**Cons:**
- High risk of subtle bugs
- Longer validation cycle
- Can't revert incrementally

### Option B: Incremental Refactor (7-10 days)
**Pros:**
- Each module can be tested independently
- Easier to validate against current behavior
- Can deploy phase-by-phase

**Cons:**
- Longer timeline
- Hybrid code is harder to maintain
- Lock contention issues harder to diagnose

### **RECOMMENDATION: Option B (Incremental)**

**Rationale:**
- Complexity is too high for a single rewrite
- Current threading + locks suggest subtle timing issues we haven't found yet
- Incremental approach lets us validate each piece before moving on

**Proposed Phase Sequence:**
1. Extract health check thread → standalone async task
2. Extract DB writer thread → async queue handler
3. Migrate rate limiter locks → `asyncio.Lock`
4. Migrate HTTP requests (urllib) → aiohttp
5. Refactor WebSocket server
6. Finally: Replace `http.server` with `aiohttp.web` handlers

---

## First Step If We Proceed

**Task 1 (P4):** Extract and refactor health check thread
- Lines ~1030–1040
- Current: daemon thread, polling loop with `time.sleep(30)`
- Target: `asyncio.create_task(health_check_loop())`
- Effort: 1–2 hours
- Validation: health status still updates correctly

**Task 2 (P4):** Extract DB writer thread
- Lines ~2005–2020
- Current: daemon thread, queue-based writes
- Target: `aiosqlite` or async queue handler
- Effort: 3–4 hours
- Validation: all DB writes still succeed, no loss

---

## Conclusion

**Migration is viable but not urgent.** The current threading approach, while not ideal, works. The benefits of asyncio (no GIL, better resource scaling) don't justify the risk unless we hit a hard performance wall.

**Recommendation for Kevin:**
- Keep current approach for launch (P1–P2)
- Schedule asyncio migration as post-launch (P3–P4)
- If we hit threading issues before launch, evaluate hybrid approaches (e.g., `asyncio` just for I/O, keep threading for sync ops)

---

## Evidence Summary

- **Threading surface:** 14 locks, 6 thread spawns, 5,554 LOC
- **Blocking patterns:** 4 sleeps, 8 SQLite calls, 14 HTTP calls, 6 threads
- **Estimated rewrite:** 500+ LOC direct conversion, 1,000+ LOC with framework swap
- **Realistic timeline:** 7–10 days incremental, 4–5 days full rewrite
- **Risk level:** HIGH (threading behavior is subtle; easy to introduce deadlocks)
- **Go/No-go:** VIABLE, but post-launch priority

---

**Next Step:** Kevin approval → Create P4 tasks for phase 1 (health check refactor)
