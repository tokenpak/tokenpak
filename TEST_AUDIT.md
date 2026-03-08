# TokenPak Test Audit — 2026-03-08

## Summary

| Metric | Count |
|--------|-------|
| Total tests | 3141 |
| Passed | 3035 |
| Failed | 24 |
| Skipped | 82 |
| Errors (setup) | 0 (fixed, see below) |
| **Pass Rate** | **96.6%** |

> Note: 6 benchmark tests in `tests/benchmarks/test_compile_performance.py` were erroring on setup due to missing `pytest-benchmark` package. Fixed by running `pip install pytest-benchmark --break-system-packages`. After fix, all 6 benchmark error tests became passing (included in 3035 passed above).

---

## Failures by Category

### Import Errors (module not yet built — 16 tests)

All from `tests/test_handoff_protocol.py`. The test file imports `TokenPak`, `HandoffBlock`, and `Handoff` from the `tokenpak` package, but these classes do not exist in `tokenpak/__init__.py` yet.

- `test_handoff_protocol.py::test_top_level_imports` — missing: `TokenPak`, `HandoffBlock`, `Handoff`
- `test_handoff_protocol.py::test_handoff_block_basic` — missing: `HandoffBlock`
- `test_handoff_protocol.py::test_handoff_block_round_trip` — missing: `HandoffBlock`
- `test_handoff_protocol.py::test_token_pak_add_and_get` — missing: `TokenPak`
- `test_handoff_protocol.py::test_token_pak_chaining` — missing: `TokenPak`
- `test_handoff_protocol.py::test_token_pak_blocks_by_type` — missing: `TokenPak`
- `test_handoff_protocol.py::test_token_pak_remove` — missing: `TokenPak`
- `test_handoff_protocol.py::test_token_pak_to_prompt_empty` — missing: `TokenPak`
- `test_handoff_protocol.py::test_token_pak_to_prompt_format` — missing: `TokenPak`
- `test_handoff_protocol.py::test_token_pak_round_trip` — missing: `TokenPak`
- `test_handoff_protocol.py::test_handoff_wire_basic` — missing: `HandoffBlock`, `Handoff`
- `test_handoff_protocol.py::test_handoff_wire_round_trip` — missing: `HandoffBlock`, `Handoff`
- `test_handoff_protocol.py::test_handoff_wire_invalid_json` — missing: `HandoffBlock`, `Handoff`
- `test_handoff_protocol.py::test_handoff_wire_unknown_version` — missing: `HandoffBlock`, `Handoff`
- `test_handoff_protocol.py::test_handoff_wire_metadata` — missing: `HandoffBlock`, `Handoff`
- `test_handoff_protocol.py::test_autogen_handoff_wire_round_trip` — missing: `HandoffBlock`, `Handoff`

### Logic / Behavior Errors (not fixed — 8 tests)

These tests pass imports but fail on assertions. Not my job to fix logic, just documenting.

**test_connection_pool.py (1)**
- `test_proxy_server_stop_closes_pool` — `AssertionError: assert ['api.anthropic.com'] == []`
  Provider not removed from `active_providers` list after pool stop.

**test_streaming.py (3)**
- `test_streaming_x_accel_buffering_header` — Expected `X-Accel-Buffering: no` header, got empty string
- `test_streaming_headers_enforced_without_upstream_content_type` — Missing `text/event-stream` Content-Type on streaming responses
- `test_streaming_headers_enforced_without_upstream_cache_control` — Missing `no-cache` Cache-Control on streaming responses

**test_serve_multiworker.py (1)**
- `TestWorkerLifecycle::test_ingest_works_under_workers` — `HTTP Error 404: Not Found` when posting to `/ingest` endpoint under multi-worker mode. Endpoint likely not registered.

**test_trackedge_features.py (2)**
- `TestPaceMetricsAndSpeed::test_calculate_pace_metrics` — Expected `avg_pacefigure=94.0`, got `0`. Logic not implemented.
- `TestPaceMetricsAndSpeed::test_speed_score_field_relative_normalization` — Speed score normalization logic returns unexpected result.

### Missing Dependency (fixed — 6 errors → 0)

`tests/benchmarks/test_compile_performance.py` required `pytest-benchmark` fixture. Fixed:
```bash
pip install pytest-benchmark --break-system-packages
```
All 6 benchmark tests now pass (confirmed re-run).

### Unresolvable / Blocked
None — all failures are either import-errors (module not yet built) or logic errors (not in scope).

---

## Final State After Fixes

```
24 failed, 3035 passed, 82 skipped in 139s
Pass rate: 96.6%
```

Run to verify:
```bash
cd ~/tokenpak && python3 -m pytest tests/ --tb=no -q
```
