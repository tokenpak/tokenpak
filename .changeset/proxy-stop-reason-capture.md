---
---

fix(proxy): capture stop_reason on proxy responses so refusals recorded as
HTTP 200 are distinguishable from successful completions.

- **New `stop_reason` column on `monitor.db` `requests`** (additive
  `ALTER TABLE`, `TEXT DEFAULT ''`, idempotent migration - same pattern as
  the existing cache/TTL/reasoning column migrations). `''` sentinel means
  "not observed" (legacy rows, errored/truncated streams) - never
  fabricated.

- **Non-streaming path:** `stop_reason` is read from a copy of the response
  JSON after the original bytes have been forwarded to the client.

- **Streaming (SSE) path:** `stop_reason` is extracted from the buffered
  `message_delta` event (`delta.stop_reason`) after the stream has been
  forwarded; parsing is handled by an internal helper in `proxy.streaming`.

- **Byte-preservation unchanged:** both observations parse copies only -
  forwarded request/response bytes are never re-serialized or modified.

- **Backward compatible:** `Monitor.log()` gains an optional
  `stop_reason=""` keyword; existing call sites without it keep working
  and insert the `''` sentinel.
