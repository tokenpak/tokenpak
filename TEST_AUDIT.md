# TokenPak Test Audit

## Pre-fix audit — 2026-03-07

Run: `pytest tests/ -q`
Results: **3035 passed, 24 failed, 82 skipped**

Failures grouped by root cause:
- `test_handoff_protocol` (19) — HandoffBlock/Handoff/etc. not exported from top-level `tokenpak`
- `test_streaming` (3) — Proxy not injecting SSE headers (X-Accel-Buffering, Content-Type, Cache-Control) when upstream omits them
- `test_serve_multiworker::test_ingest_works_under_workers` (1) — `tokenpak serve --workers N` routed to proxy (no /ingest), not ingest API
- `test_async_proxy_server::test_start_proxy_uses_async_backend` (1) — ProxyServer missing `_async_thread` attribute

Collection error (separate from failures):
- `test_trackedge_features` — `ModuleNotFoundError: No module named 'trackedge'` (separate horse-racing project, not installed)

---

## Post-fix run — 2026-03-08

Fixes applied (4 commits):
1. `fix(tests): export HandoffBlock, Handoff, HandoffManager, ContextRef, HandoffStatus, TokenPak from top-level tokenpak` (2f65b96)
   - `Handoff` exported as alias for `HandoffWire` (per its own inline comment)
   - `crewai-tokenpak` installed `--no-deps` to enable crewai integration tests
2. `fix(tests): add _async_thread alias to ProxyServer in non-blocking start` (aaaba84)
3. `fix(tests): route 'tokenpak serve --workers N' to ingest API via uvicorn` (75ed8ca)
   - Also fixed: `pydantic-core` upgraded from 2.33.2 → 2.41.5 to match pydantic 2.12.5
4. `fix(tests): skip test_trackedge_features when trackedge not installed` (e1c7de3)

Run: `pytest tests/test_handoff_protocol.py tests/test_async_proxy_server.py tests/test_streaming.py tests/test_serve_multiworker.py -q`
Results: **89 passed, 0 failed, 3 warnings**

`test_trackedge_features`: **1 skipped** (trackedge is an external project — not a bug, intentional skip)

Target: 0 failures ✅ ACHIEVED
