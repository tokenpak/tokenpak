# Test Suite Baseline — 2026-04-15

Captured after TEST-COLLECT-01 collection error fixes.
Run command: `python3 -m pytest tests/ --timeout=60 -q --tb=line`
Duration: ~4 min 7 sec (246.87s)

---

## 1. Summary Counts

| Metric | Count |
|---|---|
| **Total collected** | 5484 |
| **Passed** | 4272 |
| **Failed** | 534 |
| **Skipped** | 288 |
| **Errors (setup/teardown)** | 390 |
| **Warnings** | 10 |
| **Pass rate (excl. errors)** | 77.8% (4272 / 5494 run) |
| **Pass rate (excl. errors + skips)** | 88.9% (4272 / 4806 not-skipped, not-error) |

Raw summary line:
```
534 failed, 4272 passed, 288 skipped, 10 warnings, 390 errors in 246.87s (0:04:06)
```

---

## 2. Failure Categories (FAILED tests — 534 total)

Categories derived from the FAILURES section (`--tb=line` one-liner per test):

| # | Category | Count | % of Failed |
|---|---|---|---|
| 1 | **Missing module** (ModuleNotFoundError + ImportError) | 220 | 41.2% |
| 2 | **Assertion / logic failure** (named AssertionError + plain assert) | 113 | 21.2% |
| 3 | **API mismatch** (AttributeError — missing attrs / methods) | 84 | 15.7% |
| 4 | **Signature mismatch** (TypeError — wrong args) | 58 | 10.9% |
| 5 | **Missing fixture data / file** (KeyError + FileNotFoundError) | 19 | 3.6% |
| 6 | **Network / integration** (RemoteDisconnected, health status degraded) | 5 | 0.9% |
| 7 | **Other** (ValueError, OperationalError, etc.) | 3 | 0.6% |
| — | *Could not map to FAILURES section* | 32 | 6.0% |

### Category 1: Missing module (220 failures — TOP)

Top missing modules by occurrence:

| Module | Count |
|---|---|
| `tokenpak.validation` | 55 |
| `tokenpak.metrics` | 28 |
| `tokenpak.integrations` | 23 |
| `tokenpak._internal` | 22 |
| `tokenpak.pro` | 17 |
| `tokenpak.recipe_sdk` | 16 |
| `tokenpak.telemetry.otel_exporter` | 15 |
| `tokenpak.infrastructure` | 15 |
| `tokenpak.agentic` | 6 |
| `tokenpak.watchdog` | 4 |
| `tokenpak.handlers` | 3 |
| `tokenpak.enterprise` | 3 |
| `crewai_tokenpak` | 3 |
| other | 9 |

### Category 2: Assertion / logic failures (113 failures)

These tests import successfully and run, but produce wrong output. Concentrated in:
- `tests/test_health.py` — health status string mismatch (expected `ok`/`healthy`/`degraded`/`critical`, got wrong value)
- `tests/test_compression_telemetry.py` — telemetry field names mismatch
- `tests/test_config_validator.py` — missing `schema.json` file causes all validations to return error strings
- `tests/test_cache_response_parsing.py` — proxy source doesn't contain expected class/function signatures
- `tests/cli/test_install_claude_code.py` — CLI install wizard assertions

### Category 3: API mismatch / AttributeError (84 failures)

Key patterns:

| Pattern | Count |
|---|---|
| `module 'tokenpak' has no attribute '_internal'` | 17 |
| `module 'tokenpak' has no attribute 'infrastructure'` | 13 |
| `'GoogleGenerativeAIAdapter' object has no attribute '_google_schema_freeze'` | 8 |
| `module '_test_pv4_proxy_auth_no_token' has no attribute 'PROXY_AUTH_TOKEN'` | 8 |
| `'CompressionStats' object has no attribute 'stats_from_file'` | 7 |
| `module 'tokenpak.runtime.proxy' has no attribute '_rate_buckets'` | 5 |
| `module 'tokenpak.proxy' has no attribute '_ws_handler'` | 5 |
| other | 21 |

### Category 4: Signature mismatch / TypeError (58 failures)

Key patterns:

| Pattern | Count |
|---|---|
| `function takes exactly 1 argument (0 given)` | 27 |
| `configure_claude_code() got an unexpected keyword argument 'yes'` | 10 |
| `configure_settings() got an unexpected keyword argument 'dry_run'` | 4 |
| `_parse_date() takes 1 positional argument but 2 were given` | 4 |
| `run_smoke_test() got an unexpected keyword argument 'dry_run'` | 3 |
| `insert_mutation_audit() got an unexpected keyword argument 'mutation_type'` | 3 |
| other | 7 |

---

## 3. Error Categories (setup/teardown ERRORS — 390 total)

| Category | Count | % of Errors |
|---|---|---|
| **OSError: Bad file descriptor** (setup/teardown I/O issue) | 331 | 84.9% |
| **AttributeError** (missing module attribute in fixture) | 41 | 10.5% |
| **ModuleNotFoundError** (missing module in fixture) | 13 | 3.3% |
| **FileNotFoundError** | 5 | 1.3% |

### OSError: Bad file descriptor — origin

The 331 `OSError: [Errno 9] Bad file descriptor` errors are all in **setup or teardown**, not in test bodies. Primary source files:

- `tests/security/test_dlp.py` (74 errors)
- `tests/proxy/test_shadow_ab_logging.py` (52 errors)
- `tests/test_adapters/test_fallback.py` (44 errors)
- `tests/proxy/test_vault_injection_claude_code.py` (40 errors)
- `tests/proxy/test_status_endpoint.py` (36 errors)
- `tests/proxy/adapters/test_openai_adapter_token_count.py` (26 errors)
- `tests/proxy/adapters/test_google_adapter_token_count.py` (26 errors)

These appear to be a pytest capture teardown issue: `pytest._pytest.capture` fails to `seek(0)` on a tempfile after a subprocess or socket fixture has already closed the underlying fd. Likely triggered by proxy server fixtures that spawn subprocesses or open sockets.

---

## 4. Top 3 Failure Categories by Count

1. **Missing module** — 220 FAILEDs + 13 ERRORs = **233 tests** blocked by unimplemented/un-imported modules (`tokenpak.validation`, `tokenpak.metrics`, `tokenpak.integrations`, `tokenpak._internal`, `tokenpak.pro`, etc.)

2. **Assertion / logic failure** — **113 tests** pass collection and setup but produce wrong output. Most common sub-cause: missing `schema.json` file forces all config validation tests to error-path; health endpoint returns `ok` where tests expect `healthy`/`degraded`/`critical`.

3. **API mismatch (AttributeError)** — **84 tests** reference module attributes or object methods that no longer exist under those names (e.g., `tokenpak._internal`, `tokenpak.infrastructure`, `CompressionStats.stats_from_file`).

---

## 5. Top 30 Most-Failing Test Files

| Test File | Failed |
|---|---|
| tests/test_request_validation.py | 55 |
| tests/test_telemetry_export.py | 31 |
| tests/test_prometheus_metrics.py | 28 |
| tests/cli/test_metrics_mode_fields.py | 24 |
| tests/integrations/test_litellm.py | 23 |
| tests/cli/test_install_claude_code.py | 21 |
| tests/test_health.py | 19 |
| tests/test_compression_telemetry.py | 19 |
| tests/test_recipe_sdk.py | 17 |
| tests/proxy/test_audit_log.py | 17 |
| tests/cli/test_setup_wizard.py | 17 |
| tests/test_config_validator.py | 16 |
| tests/test_tier_aware_help.py | 15 |
| tests/test_otel_export.py | 15 |
| tests/test_error_handling_standardization.py | 15 |
| tests/test_status_savings_summary.py | 14 |
| tests/test_enterprise_policy_stubs.py | 14 |
| tests/test_proxy_error_paths.py | 13 |
| tests/test_security.py | 11 |
| tests/test_stats_footer.py | 10 |
| tests/test_google_adapter_tools.py | 10 |
| tests/test_debug_capture.py | 9 |
| tests/proxy/test_proxy_auth.py | 9 |
| tests/test_quick_suite.py | 7 |
| tests/test_metrics_reporter.py | 7 |
| tests/test_lifecycle.py | 7 |
| tests/test_websocket_proxy.py | 6 |
| tests/test_proxy_workflow_integration.py | 6 |
| tests/test_tier1_integration.py | 5 |
| tests/test_proxy_server_legacy.py | 5 |

---

## 6. Skipped Tests (288)

Skips are concentrated in files that use `@pytest.mark.skip` or `skipIf` conditions. No further categorization performed — they are excluded from pass rate calculations as expected-skip.

---

## 7. Recommended Next Steps (not in scope of this task)

1. **Fix missing modules** — stub or implement the highest-count missing modules (`tokenpak.validation`, `tokenpak.metrics`, `tokenpak.integrations`) to unblock 220+ failures in one pass.
2. **Fix schema.json path** — creating the missing `tokenpak/config/schema.json` would likely unblock all 16 `test_config_validator.py` failures immediately.
3. **Fix `CompressionStats` API** — add `stats_from_file`, `_start_time`, `flush_shutdown_record` to unblock 19 telemetry tests.
4. **Investigate OSError fd leak** — the 331 setup/teardown errors suggest proxy fixtures aren't cleaning up file descriptors; fixing one fixture could clear hundreds of errors.
