# Test Env-Dependency Matrix

Documents the env-bound pytest markers for the tokenpak test suite.
Defined in `pyproject.toml [tool.pytest.ini_options].markers` and documented in `tests/conftest.py`.

---

## Hermetic developer run (hermetic-friendly)

```bash
pytest -m 'not needs_proxy and not needs_webhook and not needs_internal_alerts \
           and not needs_cali_env and not needs_fast_host' --tb=short -q
```

This excludes all env-dependent tests. Safe to run on any host without a proxy daemon,
API keys, internal modules, or <dev-host>-specific filesystem layout.

---

## Marker definitions

### `needs_proxy`

**Requires:** A running tokenpak `ProxyServer` instance (in-process or subprocess).

Tests marked `needs_proxy` either start a `ProxyServer` directly via fixture, spin up
`tokenpak serve --workers N` as a subprocess, or connect to a proxy already running
on a known port. These tests will hang or fail on a developer box with no proxy.

**How to run (proxy-only subset):**
```bash
pytest -m needs_proxy --tb=short -q
```

**Representative tests:**

| File | Scope | Notes |
|------|-------|-------|
| `tests/test_proxy_health.py` | whole module | /health endpoint integration tests |
| `tests/test_e2e_proxy.py` | whole module | end-to-end forwarding with stub upstream |
| `tests/test_proxy_server_legacy.py` | whole module | /health, /stats, /recent endpoints |
| `tests/test_serve_multiworker.py` | whole module | subprocess `tokenpak serve --workers N` |
| `tests/test_async_proxy_server.py` | whole module | Starlette/uvicorn async proxy |
| `tests/test_first_request.py` | whole module | start + ANTHROPIC_API_KEY smoke test |
| `tests/benchmarks/test_load_100rps.py` | whole module | informational wrappers around the governed runner (also `needs_fast_host`) |
| `tests/benchmarks/neutral_health_benchmark.py` | explicit CLI only | governed O3/O6/V11 runner; starts fresh artifact-bound proxy processes |
| `tests/test_graceful_shutdown.py` | `TestShutdownRejects503`, `TestInFlightCompletion`, `TestSignalHandling`, `TestHealthDuringShutdown`; `test_proxy_stop_flushes_to_disk`; `test_drain_timeout_actually_used` | per-class/per-test; `TestGracefulShutdown` is pure |
| `tests/test_circuit_breaker.py` | `TestHealthEndpointCircuitBreakers`, `TestCircuitBreakersEndpoint` | per-class; state-machine tests are pure |
| `tests/test_connection_pool.py` | `test_proxy_server_health_includes_pool_metrics` | per-test; pool unit tests are pure |
| `tests/test_lifecycle.py` | 6 specific tests using `live_proxy` fixture or starting `HTTPServer` | per-test |

---

### `needs_webhook`

**Requires:** A live external API key or webhook endpoint (e.g. `ANTHROPIC_API_KEY`).

Tests that make real outbound HTTP calls to Anthropic or another provider.
Will skip or fail without valid credentials.

**How to run:**
```bash
ANTHROPIC_API_KEY=sk-... pytest -m needs_webhook --tb=short -q
```

**Representative tests:**

| File | Notes |
|------|-------|
| `tests/test_first_request.py` | whole module (also `needs_proxy`); skips if `ANTHROPIC_API_KEY` unset |

---

### `needs_internal_alerts`

**Requires:** `tokenpak._internal.alerts` (internal-only module, not shipped in OSS).

The `tokenpak.alerts` package is a shim that imports from `tokenpak._internal.alerts`.
If that module is absent the whole `tokenpak.alerts` package fails to import.
The affected test file already has a `skipif` guard; the marker makes the dependency
explicit and filterable via `-m`.

**How to run (on a host with internal modules):**
```bash
pytest -m needs_internal_alerts --tb=short -q
```

**Representative tests:**

| File | Notes |
|------|-------|
| `tests/alerts/test_delivery.py` | whole module; also has `skipif` guard for safe collection |

---

### `needs_cali_env`

**Requires:** <dev-host>-specific filesystem paths (`/home/<user>/tokenpak`).

These tests hardcode `/home/<user>/tokenpak` as the project root (either via
`sys.path.insert` or by treating the dev-host's `proxy.py` as mandatory). They pass on
<dev-host> and may import-error or silently skip the <dev-host>-mandatory test cases on
<shared-host> or any other host.

**How to run (on <dev-host> only):**
```bash
pytest -m needs_cali_env --tb=short -q
```

**Representative tests:**

| File | Notes |
|------|-------|
| `tests/test_optimize.py` | `sys.path.insert(0, "/home/<user>/tokenpak")` |
| `tests/proxy/test_cache_control_ttl_ordering_regression.py` | <dev-host> `proxy.py` is mandatory for Cases A–F |
| `tests/proxy/test_semantic_cache_streaming_regression.py` | loads top-level `proxy.py` from `_PROJECT_ROOT` |

---

### `needs_fast_host`

**Requires:** A host with sufficient CPU/IO throughput for timing-sensitive
informational checks. This marker does not qualify a host for a governed V11
release verdict.

The governed health runner classifies the actual machine against
`tests/benchmarks/REFERENCE.md`. A nonmatching developer or shared CI host may
produce informational evidence but cannot emit an absolute V11 pass. The 500 ms
V11 ceiling is fixed and is never widened for CI.

**How to run (on dev-host (with <dev-host>-style layout) / dev-host (alt) only):**
```bash
pytest -m needs_fast_host --tb=short -q
```

**Representative tests:**

| File | Notes |
|------|-------|
| `tests/benchmarks/test_load_100rps.py` | informational `/health` and `/stats` wire checks using the governed runner scheduler/readiness implementation (also `needs_proxy`); not release evidence |
| `tests/benchmarks/test_proxy_sdk_performance.py` | memory and latency regression targets |

---

## Governed health-release benchmark

The health-release runner is deliberately separate from ordinary pytest timing
checks:

```bash
pytest tests/benchmarks/test_health_benchmark_contract.py -q
python tests/benchmarks/neutral_health_benchmark.py matrix --help
```

`test_health_benchmark_contract.py` is hermetic, provider-free, and runs on
every benchmark workflow. It proves fail-closed handling for suite/reference/
runner/artifact drift, corrupt or incomplete vectors, threshold drift,
generator saturation, throughput failure, active readiness, phase separation,
listener counters, dependency drift, missing telemetry, manifests, and matrix
cardinality.

The `matrix` command is the release-captain surface. It requires exact control
and candidate wheel specifications, hashes of the runner, suite file, and
reference file, hashes of both subject specifications, and a content-addressed
runtime image. Each subject specification names a separately hash-bound
`tokenpak-build-provenance/v1` JSON receipt containing exactly the artifact
SHA-256, source commit, source tree, and wheel payload-manifest SHA-256. The
runner additionally compares every non-metadata wheel payload member against
that Git commit. It executes at least five measurements per artifact in
alternating control/candidate order. Its default modes are:

| Mode | Contract | Dataset posture |
|------|----------|-----------------|
| `o3` | O3a listener admission + O3b first valid health | tracked cold vectors; no numeric release threshold |
| `o6a` | open-loop load beginning at process launch | tracked startup-admission vector |
| `o6b` | listener-edge burst before readiness | tracked listener-admission vector |
| `v11` | active readiness, 20-request warm-up, then 500 requests at 100 rps with 20 workers | release-blocking only on the reference profile |

Use `--require-reference-verdict` for release evidence. Without that flag, a
non-reference host can complete an informational matrix but cannot produce an
absolute pass. Neither path requires API keys or permits provider traffic.

---

## Marker combination reference

| Goal | Command |
|------|---------|
| Hermetic dev run (no env deps) | `pytest -m 'not needs_proxy and not needs_webhook and not needs_internal_alerts and not needs_cali_env and not needs_fast_host'` |
| Proxy integration only | `pytest -m needs_proxy` |
| <dev-host> full run | `pytest -m 'not needs_webhook'` |
| Fast CI smoke | `pytest -m 'quick and not needs_proxy'` |
| All env markers except fast-host | `pytest -m 'not needs_fast_host'` |

---

## Notes

- The 22 collection errors present in the suite (as of 2026-04-10) are from new test files
  introduced by subsequent initiative commits (routing, enterprise, license, circuit-breaker).
  They are pre-existing and outside the scope of this matrix.
- Markers are advisory, not enforcing: a test marked `needs_proxy` will still run if you
  include it in your filter — it just may hang or fail. The marker only controls `-m` selection.
- `needs_fast_host` is not a reference-host attestation. Only the governed
  runner plus a matching `REFERENCE.md` profile can produce V11 release evidence.
- `needs_cali_env` tests on <shared-host> may import-error or silently test only the <shared-host> path.
  The `test_cache_control_ttl_ordering_regression.py` tests <shared-host>'s `proxy.py` as optional
  (skipped if absent) and the dev-host's as mandatory.
