---
---

Release-gate: public-API snapshot reconcile for v1.7.1.

Regenerates the public-API snapshot to match the v1.7.1 source surface.

Additions (public surface now captured):

- `tokenpak.companion.stream.*` truncated-stream guard surface
  (`StreamTruncatedError`, `guarded_stream`, `guard_enabled`,
  `read_provider_errors`, `self_check`, `EVENT_KIND`, `EVENT_NAME`,
  `EVENT_SEVERITY`, `GUARD_ENV`, `STREAM_TRUNCATED_CODE`,
  `STREAM_TRUNCATED_REMEDY`) and `tokenpak.cli.commands.doctor.run_stream_check`.
- `tokenpak.telemetry.operational.rbac_auth.SNAPSHOT_GEN_ENV`.
- Previously-uncaptured public symbols: `tokenpak.proxy.config.skeleton_active`,
  `tokenpak.proxy.config.skeleton_available`,
  `tokenpak.proxy.stats.build_health_response`,
  `tokenpak.proxy.stats.build_stats_response`.

Compatibility preserved:

- `tokenpak.proxy.passthrough.CLAUDE_CODE_HEADER_ALLOWLIST` is retained as a
  backward-compatibility alias re-exporting the canonical
  `tokenpak.proxy.headers` allowlist, so the historical import path
  `from tokenpak.proxy.passthrough import CLAUDE_CODE_HEADER_ALLOWLIST`
  continues to work unchanged.

The snapshot is regenerated in the canonical release environment (the union of
non-agent-framework extras) so that optional and third-party-conditional symbols
(e.g. `websocket_proxy.WebSocketServerProtocol`, `vector_local.faiss`) are
captured consistently with the release-gate check. No public symbol is removed.
