# TokenPak v1.0.0 Release Notes

**Release Date:** March 6, 2026
**Version:** 1.0.0 (stable)
**PyPI:** `pip install tokenpak==1.0.0`
**Migration Guide:** [See section below](#migration-guide)

TokenPak v1.0.0 is the first stable, production-ready release of the context compression engine and LLM proxy. After an internal beta (v0.9.0) and release candidate (v1.0.0-rc1), this release delivers a fully deterministic compression pipeline, async proxy server, intelligent model routing, and local cost tracking — all with zero data leaving your machine.

---

## What's New

### Compression Engine

The compression pipeline is the heart of TokenPak. v1.0 ships a fully deterministic segment → fingerprint → compress → budget → assemble pipeline with measurable results:

- **27–49% average token reduction** on real workloads (4,000+ production requests over 7 days)
- **Hybrid compression mode** — balances compression ratio against semantic preservation, adapting to content type
- **Style contracts** — content classified as `PROTECTED`, `NARRATIVE`, `CODE`, or `CONFIG` so compression respects structure (code blocks aren't mangled, protected text isn't touched)
- **CANON deduplication** — cross-turn content blocks are deduplicated using hash references, eliminating repeat context
- **BM25 vault injection** — local semantic search over your indexed knowledge base; relevant context injected automatically, zero LLM calls
- **Pluggable engines** — `HeuristicEngine` ships out of the box; LLMLingua stub available as a drop-in swap via `get_engine()`

Compile latency is enforced in CI on every PR:

| Pack Size | p50 actual | p95 target |
|-----------|-----------|------------|
| Small (~500 tokens) | **0.07ms** | < 30ms |
| Medium (~5,000 tokens) | **2.6ms** | < 50ms |
| Large (~50,000 tokens) | **22ms** | < 100ms |

### Async Proxy Server

The proxy was rewritten from `BaseHTTPRequestHandler` to a full async stack (Starlette + uvicorn + httpx), unlocking:

- **Multi-worker support** — `tokenpak serve --workers 4` scales across CPU cores
- **HTTP/2 support** via `h2`
- **Streaming SSE passthrough** — `stream: true` requests forwarded chunk-by-chunk with zero buffering, full telemetry coverage on streamed responses
- Correct `Content-Type: text/event-stream`, `Cache-Control: no-cache`, and `X-Accel-Buffering: no` headers enforced even when upstream omits them

### Provider Support

v1.0 supports OpenAI and Anthropic as first-class providers. The proxy is a transparent passthrough — your API keys stay local, credentials are never stored.

Routing optimizations are tuned out of the box:
- **claude-haiku-3** — heartbeat and lightweight tasks
- **claude-sonnet-4** — standard and long-context requests
- **claude-opus-4** — reasoning-intensive tasks only

### Caching

- **StableCache / VolatileCache / CacheRegistry** — splits system prompts into cacheable stable content vs. dynamic volatile content; Anthropic-compatible `cache_control` markers for prefix reuse
- **LRU token cache** — 25× speedup on repeated tokenization
- **Tool schema freezing** — tool schemas computed once at startup, reused verbatim
- **71.8% cache hit rate** observed in production; **6.5× cache reuse ratio** (each cached token reused 6+ times on average)

### CLI Tools

All CLI commands are zero-token (no LLM calls):

| Command | Description |
|---------|-------------|
| `tokenpak serve` | Start the compression proxy (supports `--port`, `--host`, `--workers`) |
| `tokenpak cost` | View usage, token counts, and cost reports |
| `tokenpak compress` | Compress a context file or stdin |
| `tokenpak doctor` | Diagnose configuration issues |
| `tokenpak cache` | Inspect and manage the local cache |
| `tokenpak budget` | Set and monitor spending limits |
| `tokenpak index` | Build or query the local vault index |
| `tokenpak replay` | Replay and diff past requests |

### Performance & Reliability

- **Circuit breaker pattern** — providers fault-isolated; failed upstreams are automatically bypassed with configurable backoff (6 retries, 2s delay)
- **Rate limit backoff handler** — respects provider `Retry-After` headers; exponential backoff with jitter
- **Determinism guarantee** — same input always produces same output; enforced by CI test suite on every commit
- **Estimated savings:** $341/week on a single agent deployment (production telemetry)

### Developer Experience

- **Full type hints** — 100% typed public API
- **TPK Protocol v1** — OpenClaw compatibility for Codex OAuth routing
- **LlamaIndex integration** — drop-in connector for LlamaIndex-based agents
- **Docker support** — `Dockerfile` and `docker-compose.yml` included
- **CI pipeline** — lint, format, benchmark gates, determinism tests on every PR
- **Docs** — `DEPLOYMENT.md`, `TROUBLESHOOTING.md`, `ARCHITECTURE.md`, `SECURITY.md`, `CONTRIBUTING.md`

---

## Breaking Changes

These changes affect anyone upgrading from v0.9.0 beta or v1.0.0-rc1.

### 1. Default proxy bind address changed

**Old (beta):** Proxy bound to `0.0.0.0` by default — exposed on all interfaces.
**New (v1.0):** Proxy binds to `127.0.0.1` by default.

```bash
# OLD (beta): accessible on local network
tokenpak serve

# NEW (v1.0): localhost only by default
tokenpak serve
# If you need external access, explicitly pass --host:
tokenpak serve --host 0.0.0.0
```

**Why:** Prevents unintended network exposure. External binding now requires an explicit `--host` flag.

### 2. Async proxy backend

The proxy is now async (Starlette + uvicorn). Any code that instantiated `ProxyServer` directly and called `.stop()` before `.start()` will no longer raise — this is now safe. However, integration tests that relied on the synchronous `BaseHTTPRequestHandler` behavior must be updated.

### 3. `/docs` endpoint disabled in production

The API documentation endpoint (`/docs`) is now only available when the proxy is started in debug mode. Production deployments no longer expose it.

```bash
# Debug mode (enables /docs):
tokenpak serve --debug
```

---

## Migration Guide

### From v0.9.0 (Internal Beta)

1. **Uninstall old version:**
   ```bash
   pip uninstall tokenpak
   ```

2. **Install v1.0:**
   ```bash
   pip install tokenpak==1.0.0
   ```

3. **Update proxy start command** (if you had `--host 0.0.0.0` hardcoded):
   ```bash
   # No change needed if you want localhost-only (new default)
   tokenpak serve --port 8766

   # If you need external access, keep --host explicit:
   tokenpak serve --host 0.0.0.0 --port 8766
   ```

4. **Add `--workers` for multi-core deployments:**
   ```bash
   tokenpak serve --port 8766 --workers 4
   ```

5. **Update imports** (if using internal classes):
   ```python
   # OLD (beta)
   from tokenpak.engines import CompactionEngine
   
   # NEW (v1.0) — same, but now also importable from top-level
   from tokenpak import CompressionEngine  # alias for CompactionEngine
   from tokenpak.engines.base import CompactionEngine
   ```

6. **Run the migration checker** (optional):
   ```bash
   python scripts/migrate_beta_to_v1.py --path ./your_project/
   ```

7. **Verify tests pass:**
   ```bash
   pytest tests/ -x
   ```

### Common Pitfalls

- **`ConnectionRefusedError` on port 8766** — If you're connecting from a different machine and weren't passing `--host`, the new localhost default will reject external connections. Add `--host 0.0.0.0` to your serve command.
- **Streaming responses now return SSE headers** — If you were parsing raw responses and checking `Content-Type`, update your check to handle `text/event-stream`.
- **Cache database location** — The SQLite telemetry store is at `~/.tokenpak/telemetry.db`. If you're running multiple instances, they share this store.

---

## Deprecations

These features still work in v1.0 but will be removed in v1.2:

| Deprecated | Replacement | Removal Version |
|-----------|-------------|-----------------|
| `legacy_compress()` | `compress_context()` | v1.2 |
| Direct `ProxyServer(BaseHTTPRequestHandler)` subclassing | Use the async `ProxyServer` class directly | v1.2 |

You will see `DeprecationWarning` when calling these. Update before v1.2 ships (planned Q3 2026).

---

## Known Issues

| Issue | Severity | Workaround | ETA |
|-------|----------|------------|-----|
| Streaming decompression may buffer large contexts (>100k tokens) | Low — workaround available | Split large contexts before streaming | v1.1 |
| Vision token counting not yet available for Google Gemini Pro Vision | Cosmetic — affects cost estimates only | Manually estimate vision tokens | v1.1 |
| 24 tests in integration suite marked as skipped (async integration not yet implemented) | Low — doesn't affect production behavior | N/A | v1.1 |
| LlamaIndex integration missing edge-case coverage on empty index | Low | Ensure at least one doc indexed before querying | v1.1 |

---

## Acknowledgments

TokenPak v1.0 was built by Kevin Yang with execution support from Trix and Cali. Thanks to everyone who tested the beta and provided feedback.

---

*For bugs and feature requests: [GitHub Issues](https://github.com/tokenpak/tokenpak/issues)*
*For architecture details: [ARCHITECTURE.md](ARCHITECTURE.md)*
*Full changelog: [CHANGELOG.md](CHANGELOG.md)*
