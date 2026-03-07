# Changelog

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
