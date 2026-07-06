---
---

fix(proxy): persist the shutdown telemetry record — stop() called a missing method.

`ProxyServer.stop()` delegated its shutdown summary to
`CompressionStats.flush_shutdown_record(...)`, but that method was never
defined on the class, so every graceful shutdown raised `AttributeError`
inside the non-fatal telemetry-flush step and the shutdown record was
silently dropped.

- `CompressionStats.flush_shutdown_record(record)` is now implemented:
  synchronous JSONL append, durable before return (flush + fsync), and
  tolerant of write failures (counted on stderr as a dropped telemetry
  write instead of raising out of the shutdown path).
- `CompressionStats` default log path now points at
  `~/.tokenpak/compression_events.jsonl` — the file the proxy comments,
  the demo writer, and the dashboard/analytics readers already use. The
  previous default (`~/.tokenpak/compression.log`) was never written or
  read by any code path.
- `CompressionStats(log_path=...)` accepts `str` as well as `Path`.
- The previously-skipped graceful-shutdown telemetry-flush tests are
  re-enabled, plus new tests for parent-directory creation and
  write-failure tolerance.
