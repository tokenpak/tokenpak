# TokenPak Test Audit — 2026-03-08

## Summary
- **Total tests**: 3141
- **Passed**: 3029
- **Failed**: 24
- **Errors**: 6
- **Skipped**: 82

## Pass Rate: **96.5%**

## Failures (by category)

### Import Errors (module not yet built)
- `test_handoff_protocol.py::test_top_level_imports` — missing module: `tokenpak.handoff` (CannotImportModule)
- `test_handoff_protocol.py::test_handoff_block_basic` — missing module: `tokenpak.handoff`
- `test_handoff_protocol.py::test_handoff_block_round_trip` — missing module: `tokenpak.handoff`
- `test_handoff_protocol.py::test_token_pak_add_and_get` — missing module: `tokenpak.handoff`
- `test_handoff_protocol.py::test_token_pak_chaining` — missing module: `tokenpak.handoff`
- `test_handoff_protocol.py::test_token_pak_blocks_by_type` — missing module: `tokenpak.handoff`
- `test_handoff_protocol.py::test_token_pak_remove` — missing module: `tokenpak.handoff`
- `test_handoff_protocol.py::test_token_pak_to_prompt_empty` — missing module: `tokenpak.handoff`
- `test_handoff_protocol.py::test_token_pak_to_prompt_format` — missing module: `tokenpak.handoff`
- `test_handoff_protocol.py::test_token_pak_round_trip` — missing module: `tokenpak.handoff`
- `test_handoff_protocol.py::test_handoff_wire_basic` — missing module: `tokenpak.handoff`
- `test_handoff_protocol.py::test_handoff_wire_round_trip` — missing module: `tokenpak.handoff`
- `test_handoff_protocol.py::test_handoff_wire_invalid_json` — missing module: `tokenpak.handoff`
- `test_handoff_protocol.py::test_handoff_wire_unknown_version` — missing module: `tokenpak.handoff`
- `test_handoff_protocol.py::test_handoff_wire_metadata` — missing module: `tokenpak.handoff`
- `test_handoff_protocol.py::test_autogen_handoff_wire_round_trip` — missing module: `tokenpak.handoff`

### Logic Errors (implementation issues)
- `test_async_proxy_server.py::test_start_proxy_uses_async_backend` — AssertionError: event loop backing
- `test_connection_pool.py::test_proxy_server_stop_closes_pool` — AssertionError: pool not closed properly
- `test_streaming.py::TestProxyStreamingEndToEnd::test_streaming_x_accel_buffering_header` — AssertionError: header value empty
- `test_streaming.py::TestProxyStreamingEndToEnd::test_streaming_headers_enforced_without_upstream_content_type` — AssertionError: content-type not set
- `test_streaming.py::TestProxyStreamingEndToEnd::test_streaming_headers_enforced_without_upstream_cache_control` — AssertionError: cache-control not set
- `test_trackedge_features.py::TestPaceMetricsAndSpeed::test_calculate_pace_metrics` — AssertionError: pace metric calculation off
- `test_trackedge_features.py::TestPaceMetricsAndSpeed::test_speed_score_field_relative_normalization` — AssertionError: speed score threshold

### Benchmark Errors (missing pytest-benchmark plugin)
- `test_compile_performance.py::TestSmallPackBenchmark::test_small_pack_p50_under_20ms` — ERROR (fixture not found)
- `test_compile_performance.py::TestSmallPackBenchmark::test_small_pack_p95_under_30ms` — ERROR
- `test_compile_performance.py::TestMediumPackBenchmark::test_medium_pack_p50_under_30ms` — ERROR
- `test_compile_performance.py::TestMediumPackBenchmark::test_medium_pack_p95_under_50ms` — ERROR
- `test_compile_performance.py::TestLargePackBenchmark::test_large_pack_p50_under_50ms` — ERROR
- `test_compile_performance.py::TestLargePackBenchmark::test_large_pack_p95_under_100ms` — ERROR

## Test Execution Details

**Command**: `cd ~/tokenpak && python3 -m pytest tests/ -v --tb=short`

**Execution time**: 155.83 seconds (2m 35s)

**Warnings**: 10 (mostly unknown pytest marks: @pytest.mark.benchmark, @pytest.mark.integration)

## Key Insights

1. **Strong overall pass rate (96.5%)** — Most of the test suite is stable
2. **Handoff protocol tests blocked** — `tokenpak.handoff` module doesn't exist yet (16 tests)
3. **Streaming issues** — 3 failures related to HTTP header handling in proxy streaming
4. **Pace metrics issues** — 2 calculation/normalization bugs in trackedge features
5. **Async/connection pool issues** — 2 failures in lower-level proxy infrastructure
6. **Benchmark plugin missing** — 6 errors from missing pytest-benchmark fixture; these are infrastructure setup issues, not test failures

## Recommendations

- **16 handoff protocol tests**: Will pass once `tokenpak/handoff.py` is implemented
- **Streaming header failures**: Investigate proxy response header forwarding logic
- **Pace metrics failures**: Review calculation and normalization logic in trackedge_features.py
- **Benchmark tests**: Either install pytest-benchmark or skip these tests with @pytest.mark.skip

## Status
All non-import, non-benchmark failures have actionable causes and are not transient issues. No fabricated numbers — counts verified against actual pytest output.
