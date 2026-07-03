---
---

Release-gate: public-API snapshot reconcile for the v1.10.1 proxy transport
reliability fix set.

Regenerates the public-API snapshot to match the current source surface.

Additions (public surface now captured):

- `tokenpak.proxy.upstream_retry` module: `UpstreamRetryPolicy`,
  `RetryDecision`, `UpstreamTruncatedJSONError`, `RateLimitBackoff`,
  `RETRYABLE_UPSTREAM_EXCEPTIONS`, `RETRYABLE_UPSTREAM_STATUSES`,
  `NON_RETRYABLE_UPSTREAM_STATUSES`, `build_terminal_recovery_payload`,
  `extract_tip_plan_id`, `local_json_body_is_valid`,
  `persist_failed_request_metadata`, `request_is_deterministic`,
  `response_has_truncated_json`.
- Re-exports of the retry policy surface in `tokenpak.proxy.server` and
  `tokenpak.proxy.server_async`, plus
  `tokenpak.proxy.server_async.ASYNC_UPSTREAM_ACQUIRE_TIMEOUT` /
  `ASYNC_UPSTREAM_CONCURRENCY`.
- `tokenpak.proxy.connection_pool` eviction surface (evicted-client metrics
  and retire/grace configuration).

Removal:

- `tokenpak.proxy.server.MAX_UPSTREAM_RETRIES` — superseded by
  `UpstreamRetryPolicy.max_attempts` (still configured by the same
  `TOKENPAK_UPSTREAM_RETRIES` environment variable, so operator-facing
  behavior is unchanged).
