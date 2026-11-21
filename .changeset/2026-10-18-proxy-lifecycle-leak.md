---
"tokenpak": patch
---

Stop the proxy's thread, database-connection and memory growth on busy hosts.
Session economics, polled by every companion status line, re-read the whole
request ledger on each call and kept computing after the client had given up,
so request threads, open `monitor.db` connections and memory piled up until a
restart. Evaluations are now incremental, shared between identical requests
and capped (`TOKENPAK_ECONOMICS_MAX_CONCURRENT`, `TOKENPAK_ECONOMICS_WAIT_S`;
a busy poll gets `503` with `Retry-After`). Every SQLite connection on a request
or background path is closed deterministically, the cache-invalidator session
cache keeps a compact signature under a size cap
(`TOKENPAK_CACHE_INVALIDATOR_MAX_BYTES`, default 32 MiB) instead of raw request
bodies, client disconnects no longer print tracebacks, and upstream clients
share one TLS context.
