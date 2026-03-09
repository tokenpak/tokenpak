# Changelog

All notable changes to TokenPak are documented here.

This project adheres to [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] — 2026-03-09

First stable, production-ready release of TokenPak — zero-token LLM proxy with context compression,
intelligent routing, local cost tracking, and a comprehensive agentic integration layer.

### Added

- **Core compression pipeline** — deterministic segment → fingerprint → compress → budget → assemble flow with 27–49% average token reduction
- **Phase 2 compression** — citation-mapped utility scoring, context miss detection, compression calibration, tree-sitter code processor
- **Phase 3 shadow mode** — learning phase (shadow reader), autonomous broker (active routing), source adapters (URL/Notion/Git), compile-time tool orchestration, shadow reader validation
- **OCP Protocol Phase 1** — `assembler.py`, `state_manager.py`, `state_schema.json` for OpenClaw compatibility
- **OCP Protocol Phase 2** — span extractor, evidence pack, budgeter, budget config
- **Async proxy server** — replaced `BaseHTTPRequestHandler` with Starlette + uvicorn + httpx async stack
- **Multi-worker scaling** — `tokenpak serve --workers N` spawns N processes for CPU-bound workloads
- **Streaming SSE passthrough** — `stream: true` requests forwarded chunk-by-chunk with zero buffering; output tokens extracted from stream for full telemetry coverage
- **SSE header enforcement** — `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no` enforced even when upstream omits them
- **`/health` endpoint** — uptime, version, requests_total, requests_per_second, provider status
- **`/stats` endpoint** — real-time token savings breakdown
- **`/ingest` POST endpoint** — accepts documents for vault indexing, returns `{status: ok, ids: [uuid]}`
- **Prometheus `/metrics` endpoint** — `tokenpak/monitoring/health.py` + `tokenpak/api/routes.py`
- **`StableCache` / `VolatileCache` / `CacheRegistry`** — stable/volatile content split for maximum Anthropic prompt cache utilization
- **LRU token cache** — 25× speedup on repeated tokenization for identical content
- **Connector framework** — base connector + local filesystem + Obsidian + Pro tier stubs (Google Drive, Notion, GitHub)
- **Pluggable compaction engines** — `CompactionEngine` abstract base; `HeuristicEngine` ships by default; LLMLingua stub for drop-in swap
- **Full vault indexer** — extended file types, incremental re-indexing, symbol extraction, index CLI
- **Cost tracker + proxy wiring** — per-completion cost, model, and latency in SQLite telemetry store
- **Stats footer** — real savings per request (endpoint + CLI command `tokenpak last --oneline`)
- **Session filter & search** — `/v1/sessions` endpoint, `FilterBar.tsx` component, 48 tests
- **Dashboard CSV export** — `ExportAPI`, `CSVExporter`, `ExportButton`, 30 tests
- **Credential passthrough module** — API keys forwarded through proxy pipeline without storage
- **Platform adapters** — `openclaw`, `claude_cli`, `generic` adapters + registry + proxy wire-in
- **Agent learning store** — model performance, compression, utility, gap pattern tracking
- **License keygen** — RSA-4096 key generation + admin CLI (`customer_id`), 48 tests
- **Enterprise Helm chart** — production-ready Kubernetes deployment with custom recipe SDK
- **Circuit breaker pattern** — provider fault isolation; failed upstreams automatically bypassed
- **Retry/fallback intelligence** — per-error routing, config, status command, 21 tests
- **OSS recipe library** — 50 YAML compression recipes, `CompressionRecipeEngine`, demo CLI, 36 tests
- **Macro engine** — YAML macros with `create/list/run/show/delete` CLI, variable substitution, fail-fast/continue-on-error, dry-run, 47 tests
- **Macro scheduler + script hooks** — premade macros for common agent workflows
- **Fingerprint sync client** — generator, privacy controls, sync, CLI, 26 tests
- **Context handoff system** — lifecycle, expiry, CLI, 31 tests; exports `HandoffBlock`, `Handoff`, `HandoffManager`, `ContextRef`, `HandoffStatus`
- **Cost intelligence module** — trends, anomaly detection, projections, recommendations, budget alerts, 51 tests
- **A/B auto-optimizer** — auto-promote, significance testing, API; 42 tests
- **Failover engine** — error classification, circuit breaker, response normalization, failover translators (Google response + streaming), 100 tests
- **Workflow budget rebalancer** — dynamic redistribution, floor, warn/critical thresholds, 63 tests
- **Workflow CLI enhancements** — filter flag, resume plan, progress bar, ETA, 19 tests
- **Directive applier** — new directive types, caching, schema, 54 tests
- **File watcher** — `.gitignore`/`.tokenpakignore` support, systemd service, 34 tests
- **`tokenpak lock` subcommand** — file lock coordination + `renew` method
- **Trigger CLI** — `list/add/remove/test/log` with `--json`, 28 tests; git hooks, agent events, fire + hook CLI
- **Anonymous metrics reporter** — opt-in daily batch, content-stripped privacy model
- **Phase 5 integration** — `HeartbeatIngest` + `QueryBriefing` modules; JSONL fallback + heartbeat_ingest verified
- **`tokenpak doctor`** — comprehensive diagnostics with color output, fleet doctor variant
- **Replay CLI** — `list/show/run` with `--model`, `--no-compress`, `--aggressive`, `--diff` flags
- **`__main__.py`** — enables `python -m tokenpak` entrypoint

### Changed

- **Routing optimization** — haiku for heartbeats, sonnet for long-context, opus for reasoning-only; tuned backoff (6 retries, 2s base delay)
- **Proxy graceful shutdown** — SIGTERM handler + in-flight drain before exit
- **PyPI metadata** — classifiers, badges, keywords optimized for discoverability
- **Version bump** — `1.0.0-rc1` → `1.0.0` after final CI validation
- **Default proxy bind** — changed from `0.0.0.0` to `127.0.0.1`; external access requires `--host 0.0.0.0`

### Fixed

- Fixed critical proxy uncaught exceptions — BUG-002 C1/C2/C3 regression cases
- Fixed `ProxyServer.stop()` — now closes connection pool even if server was never started
- Fixed streaming proxy path to enforce SSE headers (`X-Accel-Buffering`, `Content-Type`, `Cache-Control`)
- Fixed top-level `__init__.py` exports — `HandoffBlock`, `TokenPak`, `Handoff`, `HandoffManager`, `ContextRef`, `HandoffStatus` now accessible
- Fixed `filter_comparable_races` — skip criteria when race or PP is missing field data (prevents crash on incomplete data)
- Fixed `tokenpak-local` `utils.py` — removed conditional import; use local shims always
- Fixed CI pipeline — added missing `watchdog` dependency, resolved lint/format failures, fixed `ProxyServer.stop()` bug
- Fixed `test_tampered_signature_rejected` — mid-char flip instead of last char (base64 padding edge case)
- Fixed docstring version `v0.1.0` → `v1.0.0` for PyPI consistency
- Fixed rate limit backoff handler — removed initial implementation (caused 70s stalls on 429s); re-implemented with proper jitter and tuning

### Docs

- `ARCHITECTURE.md` — comprehensive system overview (219 lines)
- `CONTRIBUTING.md` — contribution guide, PR process, issue templates
- `DEPLOYMENT_CHECKLIST.md` — v1.0 pre-release checklist
- Adapter compatibility matrix — TokenPak v1.0 × OpenAI/Anthropic/LiteLLM/LangChain/LlamaIndex/CrewAI/AutoGen/Langfuse
- Adapter coverage matrix — telemetry, LiteLLM, agent, with coverage percentages
- SDK quick-start guide, install guide, and basic compression example
- Live benchmarks snapshot (2026-03-08, v0.4.0)
- v1.0 release notes + migration script (`RELEASE_NOTES_v1.0.md`)
- `HEALTH_AUDIT_2026-03-09.md` — health endpoint audit report
- Merge conflict resolution guide + automation script
- Glossary tooltips — `term_cards.json`, `glossary.js/css/html`, 18 tests
- `PYPI_READINESS_REPORT.md` — tokenpak-vectordb publication readiness
- README refreshed with engines, connectors, benchmark results, build badges

### Tests

- Integration test suite — 80 comprehensive tests across 6 files (`cali: integration test suite`)
- Phase 4 telemetry tests — +90 tests; openai/anthropic/gemini adapters at 99–100% coverage
- Phase 4 integration & chaos test suite — 53 new tests + CI pipeline + breaking change detection
- `tool_schema_registry` unit tests — 22 passing
- Proxy error path integration tests — C1/C2/C3 regression coverage
- Determinism test suite + CI workflow — enforces same-input=same-output on every PR
- `test_cost_budget_cli.py` — 36 tests (QA rework)
- `test_storage.py` — 37 tests for Phase 7H schema patch
- `test_vault_indexer_full.py` — full file type support, symbol extraction, binary skip
- Vectordb integration tests — 18 passing
- `TEST_AUDIT.md` — 3,035 passed, 24 skipped (async integration), 0 failures post-fix

### Infrastructure

- CI pipeline — lint (ruff), format (black), benchmark gate, determinism checks on every PR
- Docker support — `Dockerfile` and `docker-compose.yml`
- SPDX-License-Identifier: MIT headers added to all core modules
- Build status + coverage badges added to README
- `push-verified.sh` — dual remote push with SSH verification
- `pytest` markers registered — `integration`, `chaos`, `slow`, `flaky`
- Full mypy type hints sprint — reduced errors from 151 → 92 across main package

### Cache Sprint (2026-03-09)

- **P0 — `apply_stable_cache_control` wired into ProxyServer pipeline** (`a1b3f45`) — every LLM request now automatically classifies system blocks as stable/volatile and attaches `cache_control: ephemeral` to the last stable block before forwarding to Anthropic
- **P1 — Cache poison removal** (`de9099d`) — frozen tool schemas via `ToolSchemaRegistry` singleton (deterministic, byte-identical per request); `datetime.now()` and `uuid.uuid4()` calls audited and removed from all prompt-building paths; stable prefix is now bit-identical across consecutive requests
- **P1 — Cache telemetry** (`06da64e`, `7801c81`) — `CacheMetrics` captures `cache_read_input_tokens` per request; FRESH/CACHED status + token counts logged per response; `/v1/cache/stats` endpoint exposes aggregate hit rate, miss counts, and cache size
- **Validated: 61.1% cache hit rate** and **10.2× efficiency improvement** measured post-sprint (from ~10% with cache poison, ~50% after poison removal alone, 61.1% with full stable prefix pipeline)

---

## [1.0.0-rc1] — 2026-03-05

Release candidate for v1.0.0. All features complete; focus was hardening, CI, and documentation.

### Added
- Version bumped `1.0.0-rc1` → `1.0.0` after final CI validation
- Determinism test suite + CI workflow
- Circuit breaker pattern for provider fault isolation
- `--workers N` flag to `tokenpak serve`
- `StableCache` / `VolatileCache` / `CacheRegistry` module
- Unit tests for `tool_schema_registry`
- Rate limit backoff handler (initial)
- PyPI metadata optimization (classifiers, badges, keywords)

---

## [0.9.0] — 2026-02-01

Internal beta. Not distributed publicly.

### Added
- Initial TokenPak core: proxy wire format, CLI skeleton, basic token budget
- Phase 5a ingest API
- Cache efficiency layer with deterministic retrieval
- Architecture validated against real OpenAI and Anthropic API workloads

---

## Links

[1.0.0]: https://github.com/kaywhy331/tokenpak/releases/tag/v1.0.0
[1.0.0-rc1]: https://github.com/kaywhy331/tokenpak/releases/tag/v1.0.0-rc1
[0.9.0]: https://github.com/kaywhy331/tokenpak/releases/tag/v0.9.0
