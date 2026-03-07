# Changelog

<<<<<<< HEAD
All notable changes to TokenPak are documented here.

This project adheres to [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- _(place new items here with PR links)_

### Changed
- _(breaking or behavioral changes)_

### Fixed
- _(bug fixes)_

### Security
- _(security patches)_

---

## [1.0.0] - 2026-03-06

First stable release of TokenPak — zero-token LLM proxy with context compression, intelligent routing, and local cost tracking.

### Added
- **Core compression engine** — segment, fingerprint, compress pipeline with 40–60% average token reduction
- **Intelligent request router** — routes to fast/cheap or powerful models based on complexity scoring
- **CLI tool** (`tokenpak serve`, `tokenpak cost`, `tokenpak compress`, `tokenpak doctor`, `tokenpak cache`) for zero-token local operations
- **Public Python API** — `TelemetryCollector`, `CacheManager`, `Budgeter`, `Calibrator`, `Compiler`, `Walker`, and `Registry` modules
- **OCP Protocol v1 support** — OpenClaw compatibility for Codex OAuth routing
- **Vault semantic index** — local file indexing with instant search (zero LLM calls)
- **Connector framework** — base connector + local filesystem + Obsidian + Pro tier stubs (Google Drive, Notion, GitHub)
- **Pluggable compaction engines** — heuristic compressor + LLMLingua stub for drop-in swapping
- **Enterprise features** — audit log, SOC2/GDPR/CCPA compliance reports, enterprise tier scaffolding
- **Feedback infrastructure** — GitHub issue templates (bug report, feature request), Discussions categories
- **Security policy** (`SECURITY.md`) — responsible disclosure process, supported versions, audit log
- **Dependency manifest** (`DEPENDENCIES.md`) — full third-party dependency list with licenses
- **Developer tooling** — `requirements-dev.txt`, `.env.example`, pre-commit hooks, pytest suite
- **Documentation** — `README.md`, `ARCHITECTURE.md`, `CONTRIBUTING.md`, `DEPENDENCIES.md`, `SECURITY.md`

### Fixed
- Edge case in empty input handling during compression
- Memory leak in cache eviction under high-throughput load
- Vault retrieval helpers re-exported from `proxy.router` for correct import path
- Default proxy binding hardened to `127.0.0.1` (was `0.0.0.0`) to prevent unintended network exposure

### Security
- Default server binding restricted to localhost; external binding requires explicit `--host` flag
- API docs endpoint (`/docs`) disabled in non-debug mode
- Input validation added to prevent prompt injection via compressed context
- No hardcoded secrets — all credentials passed via environment variables
- Dependency audit completed; no known CVEs in pinned versions as of 2026-03-06

---

## [0.9.0] - 2026-02-01

Internal beta release used for initial testing and architecture validation.

### Added
- Initial TokenPak core: proxy wire format, CLI skeleton, basic token budget
- Phase 5a ingest API implementation
- Cache efficiency layer with deterministic retrieval

### Changed
- Architecture validated against real OpenAI and Anthropic API workloads

### Notes
- Not released publicly; used internally to validate the OCP Protocol design

---

## Links

[Unreleased]: https://github.com/kaywhy331/tokenpak/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/kaywhy331/tokenpak/releases/tag/v1.0.0
[0.9.0]: https://github.com/kaywhy331/tokenpak/releases/tag/v0.9.0
=======
All notable changes to TokenPak will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0-rc1] - 2026-03-06

### 🎉 First Release Candidate

TokenPak v1.0 marks the first production-ready release of the deterministic context compression system.

### Added

#### Core Compression
- **Hybrid compression mode** — intelligently balances compression ratio vs. semantic preservation
- **Style contracts** — PROTECTED, NARRATIVE, CODE, CONFIG classifications for content-aware compression
- **BM25 vault injection** — semantic search over indexed knowledge base, injected into system prompts
- **CANON deduplication** — cross-turn content block deduplication with hash-based references

#### Caching & Performance
- **Prompt caching** — Anthropic-compatible cache_control markers for prefix reuse
- **Tool schema freezing** — generates tool schemas once at startup, reuses verbatim
- **Stable/volatile split** — separates cacheable system prompts from dynamic content
- **LRU token cache** — 25x speedup on repeated tokenization

#### Telemetry & Monitoring
- **SQLite telemetry store** — tracks requests, tokens, costs, latency per model
- **Cost/budget CLI** — `tokenpak cost`, `tokenpak budget` commands
- **Real-time stats** — `/health`, `/stats` endpoints with savings breakdown

#### CLI Tools
- `tokenpak serve` — run the compression proxy
- `tokenpak doctor` — diagnose configuration issues
- `tokenpak cost` — view usage and cost reports
- `tokenpak budget` — set and monitor spending limits
- `tokenpak index` — build/query vault index
- `tokenpak replay` — replay and diff past requests

#### Developer Experience
- **Full type hints** — 100% typed public API
- **Comprehensive docs** — DEPLOYMENT.md, TROUBLESHOOTING.md, architecture guides
- **Docker support** — Dockerfile and docker-compose.yml included

### Performance

Based on production telemetry (4,000+ requests over 7 days):
- **27% token reduction** on average
- **71.8% cache hit rate** across requests
- **6.5x cache reuse ratio** (each cached token used 6+ times)
- **$341 estimated weekly savings** on a single agent deployment

### Breaking Changes

None — this is the first stable release.

## [0.1.0] - 2026-02-15

### Added
- Initial development release
- Basic compression pipeline
- Proof-of-concept proxy server
>>>>>>> 2a1287e92675787cd8cb17653be8891a1d32243b
