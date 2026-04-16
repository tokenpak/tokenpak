# Changelog — TokenPak v1.0.0

All notable changes to TokenPak from beta through v1.0.0 are documented here.

This file follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

For a narrative overview, see [RELEASE_NOTES_v1.0.md](RELEASE_NOTES_v1.0.md).

---

## [1.0.0] — 2026-03-06

First stable, production-ready release of TokenPak.

### Features

#### Compression Engine
- **Core compression pipeline** — deterministic segment → fingerprint → compress → budget → assemble pipeline with 27–49% average token reduction on production workloads
- **Hybrid compression mode** — intelligently balances compression ratio against semantic preservation based on content type
- **Style contracts** — `PROTECTED`, `NARRATIVE`, `CODE`, `CONFIG` content classifications for type-aware compression (code blocks preserved, protected sections untouched)
- **CANON deduplication** — cross-turn content block deduplication using SHA hash references; eliminates repeated context across turns
- **BM25 vault injection** — local semantic search over indexed knowledge base, injected into system prompts at zero token cost
- **Pluggable engine API** — `CompactionEngine` abstract base; `HeuristicEngine` ships by default; LLMLingua stub for drop-in swap via `get_engine()`
- **Determinism guarantee** — same input always produces same compressed output; enforced in CI on every PR

#### Async Proxy Server
- **Async proxy rewrite** — replaced synchronous `BaseHTTPRequestHandler` with Starlette + uvicorn + httpx async stack
- **Multi-worker scaling** — `tokenpak serve --workers N` spawns N worker processes for CPU-bound workloads
- **HTTP/2 support** — via `h2` library (>=3,<5)
- **Streaming SSE passthrough** — `stream: true` requests forwarded chunk-by-chunk, zero buffering; output tokens extracted from stream for full telemetry coverage
- **SSE header enforcement** — `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no` enforced even when upstream omits them
- **`StreamHandler` class** — gzip-aware chunk buffering with usage extraction
- **`iter_sse_events()` helper** — iterate parsed events from raw SSE bytes
- **`/ingest` POST endpoint** — accepts documents for vault indexing, returns `{status: ok, ids: [uuid]}`
- **`/health` and `/stats` endpoints** — real-time proxy health and savings breakdown

#### Caching & Performance
- **`StableCache` / `VolatileCache` / `CacheRegistry`** — splits system prompts into cacheable stable content and dynamic volatile content
- **Anthropic-compatible `cache_control` markers** — enables prompt prefix caching and reuse across requests
- **Tool schema freezing** — tool schemas computed once at startup, reused verbatim (no recompute per request)
- **LRU token cache** — 25× speedup on repeated tokenization for identical content
- **Stable/volatile split** — separates cacheable system prompts from dynamic content for maximum cache utilization

#### CLI Tools
- `tokenpak serve` — start the compression proxy (`--port`, `--host`, `--workers`, `--debug` flags)
- `tokenpak cost` — view usage, token counts, and cost reports (`--week`, `--month`, `--model` filters)
- `tokenpak compress` — compress a context file or stdin, print result
- `tokenpak doctor` — diagnose configuration issues and provider connectivity
- `tokenpak cache` — inspect, warm, and manage the local LRU cache
- `tokenpak budget` — set and monitor spending limits with alert thresholds
- `tokenpak index` — build or query the local vault BM25 index
- `tokenpak replay` — replay and diff past requests for debugging

#### Telemetry & Monitoring
- **SQLite telemetry store** — tracks requests, tokens, costs, latency per model at `~/.tokenpak/telemetry.db`
- **`TelemetryCollector` / `CostTracker`** — programmatic access to per-completion cost, model, and latency data
- **`Budgeter`** — enforce token budgets; prune context to fit within configured limits
- **`Calibrator`** — calibrate compression parameters against observed telemetry

#### Reliability
- **Circuit breaker pattern** — provider fault isolation; failed upstreams automatically bypassed
- **Rate limit backoff** — respects provider `Retry-After` headers; exponential backoff with jitter (6 retries, 2s base delay)
- **`ProxyServer.stop()` safety** — now safe to call even if server was never started (closes pool cleanly)

#### Intelligence & Routing
- **Complexity-based routing** — routes requests to fast/cheap or powerful/expensive models based on request complexity scoring
- **Tuned routing defaults** — haiku for heartbeats, sonnet for long-context, opus for reasoning-only tasks
- **`RoutingLedger`** — tracks routing decisions for debugging and optimization

#### Integrations
- **TPK Protocol v1** — OpenClaw compatibility for Codex OAuth routing
- **LlamaIndex integration** — drop-in connector for LlamaIndex-based agents
- **Phase 5 integration** — `HeartbeatIngest` + `QueryBriefing` modules

#### Developer Experience
- **Full type hints** — 100% typed public API surface
- **Docker support** — `Dockerfile` and `docker-compose.yml` included
- **CI pipeline** — lint (ruff), format (black), benchmark gate, determinism test suite on every PR
- **Test suite** — 3,035 passing tests (24 skipped, pending async integration coverage)
- **PyPI-ready metadata** — classifiers, badges, keywords, URLs optimized for discoverability

#### Documentation
- `README.md` — quick start, architecture diagram, plans/pricing, performance benchmarks
- `ARCHITECTURE.md` — universal content compiler design, multimodal pipeline
- `DEPLOYMENT.md` — production deployment guide (systemd, Docker, reverse proxy)
- `TROUBLESHOOTING.md` — common issues and diagnostic commands
- `CONTRIBUTING.md` — contribution guide, PR process
- `SECURITY.md` — responsible disclosure policy, supported versions, audit log
- `DEPENDENCIES.md` — full third-party dependency list with licenses
- `API.md` — public API reference
- SDK quick-start guide, install guide, and basic compression examples

### Fixes

- Fixed edge case in empty input handling during compression (silent failure → empty result)
- Fixed memory leak in cache eviction under high-throughput load
- Fixed vault retrieval helpers re-exported from `proxy.router` for correct import path
- Fixed `ProxyServer.stop()` — now closes connection pool even if server was never started
- Fixed streaming proxy path to enforce SSE headers (`X-Accel-Buffering`, `Content-Type`, `Cache-Control`)
- Fixed `HandoffBlock`, `TokenPak`, `Handoff`, `HandoffManager`, `ContextRef`, `HandoffStatus` not exported from top-level `__init__.py`
- Fixed `filter_comparable_races` — skip criteria when race or PP is missing fields (prevents crash on incomplete data)
- Fixed CI pipeline — added missing `watchdog` dependency, resolved lint/format failures

### Breaking Changes

- **Default proxy bind address changed** from `0.0.0.0` to `127.0.0.1` — external access requires explicit `--host 0.0.0.0`
- **Async proxy backend** — proxy no longer uses synchronous `BaseHTTPRequestHandler`; direct subclassing no longer supported
- **`/docs` endpoint disabled** in non-debug mode — start with `--debug` to re-enable

### Dependencies

```
aiohttp>=3.9.0
pyyaml>=6.0
click>=8.1.0
starlette>=0.36.0
uvicorn>=0.27.0
httpx>=0.26.0
h2>=3,<5
watchdog>=3.0.0
```

Optional:
- `tiktoken>=0.5.0` — for accurate token counting (`pip install tokenpak[tokens]`)
- `mkdocs>=1.5.0`, `mkdocs-material>=9.5.0` — for docs site (`pip install tokenpak[docs]`)

Python: 3.10, 3.11, 3.12, 3.13

### Deprecations

- `legacy_compress()` — deprecated in v1.0, will be removed in v1.2; use `compress_context()` instead
- Direct `BaseHTTPRequestHandler` subclassing of `ProxyServer` — deprecated; use async `ProxyServer` directly

### Known Issues

- Streaming decompression may buffer large contexts (>100k tokens) — optimization planned for v1.1
- Vision token counting not yet available for Google Gemini Pro Vision — affects cost estimates only; fix in v1.1
- 24 integration tests marked as skipped (async integration not yet implemented) — coverage expansion in v1.1
- LlamaIndex integration missing edge-case coverage on empty index — fix in v1.1

---

## [1.0.0-rc1] — 2026-03-05

Release candidate for v1.0.0. All features complete; focus on hardening, CI, and documentation.

### Added
- Bumped version `1.0.0-rc1` → `1.0.0` after final CI validation
- Added determinism test suite + CI workflow — enforces same-input=same-output on every PR
- Added circuit breaker pattern for provider fault isolation
- Added `--workers N` flag to `tokenpak serve` — multi-process CPU scaling
- Optimized PyPI metadata: classifiers, badges, keywords (production-ready)
- Added unit tests for `tool_schema_registry` (≥4 tests passing)
- Rate limit backoff handler + 4 passing tests
- `StableCache` / `VolatileCache` / `CacheRegistry` module

---

## [0.9.0] — 2026-02-01

Internal beta release. Not distributed publicly.

### Added
- Initial TokenPak core: proxy wire format, CLI skeleton, basic token budget
- Phase 5a ingest API implementation
- Cache efficiency layer with deterministic retrieval
- Architecture validated against real OpenAI and Anthropic API workloads
- TPK Protocol design validated

### Notes
- Used internally to validate architecture before v1.0; not a public release
- Default proxy binding was `0.0.0.0` (changed to `127.0.0.1` in v1.0)

---

## Links

- [Releases on GitHub](https://github.com/tokenpak/tokenpak/releases)
- [v1.0.0](https://github.com/tokenpak/tokenpak/releases/tag/v1.0.0)
- [Migration Guide](RELEASE_NOTES_v1.0.md#migration-guide)
- [Open Issues](https://github.com/tokenpak/tokenpak/issues)
