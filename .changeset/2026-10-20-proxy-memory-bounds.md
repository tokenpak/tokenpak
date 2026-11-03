---
"tokenpak": patch
---

Keep more of the proxy's long-running memory and descriptors bounded. The
token-count cache no longer keeps a copy of every long text it counted (the
file token-count endpoint and `truncate_to_tokens` could hold hundreds of
megabytes), the cache-invalidator size cap now counts bytes rather than
characters, and the session and time forecast calibration readers share the
ledger descriptor the proxy's other readers use instead of pinning one more
`monitor.db` descriptor for the life of the process.
