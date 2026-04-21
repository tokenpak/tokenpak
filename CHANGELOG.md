# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-04-21

### Added
- **TokenPak Integration Protocol (TIP-1.0)** — canonical semantic protocol spec: canonical wire headers (`X-TokenPak-TIP-Version`, `X-TokenPak-Profile`, `X-TokenPak-Cache-Origin`, `X-TokenPak-Savings-{Tokens,Cost}`), telemetry event schema, error schema, 4 manifest schemas, 5 profiles. Reference-implementation claim (Constitution §13.3) CI-validated via `scripts/tip_conformance_check.py` against `tokenpak-tip-validator==0.1.0` on every `make check`. End users don't need the validator; it's a separate PyPI package for protocol implementers.
- **`services/` shared execution backbone** — `PipelineContext`, `Stage` protocol, `services.execute` composition, 6 stage wrappers (compression, security, cache, routing, telemetry, policy). This is the architectural home for pipeline logic.
- **MCP control-plane substrate via `services/mcp_bridge/`** — `Transport`, `LifecycleManager`, `CapabilityNegotiator`, `ToolRegistry`, `ResourceRegistry`, `PromptRegistry`; consumed by companion + SDK (no MCP library forking).
- **`sdk/mcp/`** — MCP client/server bridge with TIP-label validation.
- **Companion runtime package restructure** — `companion/{launcher,mcp_server,hooks,capsules,budget,templates}/` with backwards-compat re-exports.
- **`.importlinter` architecture-contract enforcement** — 5 contracts (tier-layering, entrypoints-reach-via-proxy-client, entrypoints-dont-import-primitives, contracts-single-home, mcp-plumbing). Runs on every `make lint-imports`.
- **Claude Code integration profiles** — 6 claude-code-* adapter profiles across CLI, TUI, tmux, SDK, IDE, cron consumption modes.
- **Credential subsystem MVP** — `tokenpak creds list` + `tokenpak creds doctor` across 5 providers with single-refresh-owner invariant.
- **20+ new subcommands** surfaced via `tokenpak help --all` (Control / Visibility / Indexing / Configuration groups).

### Changed
- **Canonical §1 subsystem layout** — `tokenpak/` now contains exactly 18 canonical subsystems (plus `intelligence/` as a documented satellite per Architecture §11.6). Achieved via 76 D1 migration bites + the agent/proxy consolidation + the agent/cli consolidation. Every legacy top-level module has a DeprecationWarning re-export shim at the old path (removal target TIP-2.0).
- **`agent/proxy/*` folded into `proxy/*`** — ~10,594 LOC; 25 files + providers subpackage; 29 legacy shims + 30 deprecation tests; byte-fidelity gate PASS.
- **`agent/cli/*` folded into `cli/*`** — 7,345 LOC; 38 files; 38 legacy shims + 39 deprecation tests; byte-fidelity gate PASS including per-subcommand help-text identity.
- **Cache-origin truthfulness contract (Constitution §5.3)** — `cache_origin` enum (`client`/`proxy`/`unknown`) enforced as a single-site invariant.
- **Public-internal boundary** — 58 boundary leaks cleaned (hourly boundary-check cron green). Hardcoded vault paths replaced with env-var-driven defaults at `~/.tokenpak/*`.
- **Monitor DB re-wired** — `ProxyServer` now `.log()`s every request; `tokenpak status` counters work again.

### Fixed
- **Anthropic `cache_control` TTL ordering** — `ttl=1h` blocks no longer appear after default-ttl in document order (cross-prompt cache hits restored).
- **Byte-fidelity on Anthropic passthrough** — JSON re-serialization was breaking Anthropic's body-byte-dependent billing routing; body is now preserved as raw bytes.
- **Compaction retry loop** (OpenClaw-embedded) — mitigated via `compaction.memoryFlush.enabled:false` + `reserveTokensFloor:24000`.

### Migration notes for 1.0.3 → 1.1.0

**Every legacy import path still works.** Modules moved to canonical homes carry DeprecationWarning re-export shims. Expected noise on first import only; shims are removed in TIP-2.0.

**Env vars (optional, defaults work out of the box):**
- `TOKENPAK_ENTRIES_DIR` (default `~/.tokenpak/entries`)
- `TOKENPAK_TEACHER_SOURCE_ROOTS` (default `~/.tokenpak/teacher-sources`)
- `TOKENPAK_COLLECT_TELEMETRY_SCRIPT` (default `~/.tokenpak/scripts/collect-agent-telemetry.py`)

**End users:** `pip install tokenpak` — that's still the whole install.

**TIP-1.0 implementers** (third-party adapter/plugin authors): `pip install tokenpak-tip-validator==0.1.0` — separate conformance package.

## [1.0.3] - 2026-04-19

### Changed
- First clean public release. Prior 1.0.x versions on PyPI (1.0.0, 1.0.1, 1.0.2) had a broken CLI entry point — the package installed but `tokenpak` raised `AttributeError` because the CLI package `__init__.py` shadowed the module implementation without re-exporting `main`.
- Fixed entry-point collision: `tokenpak/cli.py` relocated to `tokenpak/cli/_impl.py`; `tokenpak/cli/__init__.py` now re-exports `main` so `tokenpak=tokenpak.cli:main` resolves and `python -m tokenpak` works.
- Full runtime dependency set declared in `setup.py` (previously missing anthropic, openai, fastapi, litellm, llmlingua, pydantic, requests, rich, scipy, sentence-transformers, tree-sitter-languages, watchdog, cryptography, click, h2).
- Package metadata: `author="TokenPak"`, `author_email="hello@tokenpak.ai"`, `url="https://github.com/tokenpak/tokenpak"`, `python_requires=">=3.10"`, PyPI classifiers.
- `/standards` directory: 20 canonical documents (constitution, architecture, code, CLI/UX, dashboard, brand, docs, glossary, audit rubric, release quality bar, release workflow, environments, staging checklist, production runbook, post-deploy validation, rollback runbook, hotfix workflow, release comms, release log) + 6 templates.

### Removed
- Dated audit artifacts under `docs/audits/*.md` (41 files) and `docs/*-2026-03-29.md` (5 files) — consolidated or deleted per Constitution §5.6.
- `docs/deployment.md` (internal 3-host fleet runbook; use `deployments/` for public self-hosting configs).
- Committed SQLite journal files (`monitor.db-shm`, `monitor.db-wal`) — now in `.gitignore`.
- Stale `proxy_monolith.py.bak`.

### Fixed
- `tokenpak doctor` no longer hardcodes 3 internal fleet hosts as the default fleet config — empty list by default; users with multi-host deployments populate `~/.tokenpak/fleet.yaml` themselves.
- Dashboard sidebar no longer renders an internal hostname to users.
- README 30-second demo uses `tokenpak start` (actual proxy command) instead of the conflicting `tokenpak serve`. Removed the `tokenpak integrate` story — that command isn't implemented yet; users configure clients via `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL` for now.

### Deprecated
- PyPI 1.0.0, 1.0.1, 1.0.2 releases — superseded; yank recommended for the first two.

## [1.0.0] - 2026-03-18

### Added
- Core token counting with LRU caching and lazy loading
- Context compilation pipeline (multi-mode)
- Wire format generator (TokenPak Protocol v1.0)
- CLI with parallel processing and batch operations
- Heuristic rule-based compression engine
- LLMLingua ML-powered compression engine
- Content processors: text, code (regex + tree-sitter), data (JSON/CSV/YAML/TOML)
- Data connectors: local filesystem, Obsidian vault, Git repos
- Advanced connectors: GitHub, Google Drive, Notion, URL fetcher
- CANON block registry and context assembly
- STATE_JSON management with patch applicator
- Evidence span extraction and pack wire format
- Response contract validation with auto-repair
- JSON schemas for TokenPak Protocol v1.0 (block, compiled, evidence)
- Context budget tiers with quadratic allocation
- Task complexity scoring and intent classification
- ELO-based model ranking
- Calibration (auto-adjusting compression)
- Configurable routing rules engine with fallback chains
- Debug trace side-channel (X-TokenPak-Trace header)
- Coverage gap detection (miss detector)
- Shadow mode transaction logging and validation
- Self-hosted telemetry: SQLite storage, ingest API, processing pipeline
- Canonical event normalization
- Provider pricing engine
- Content segmentization
- Hourly/daily rollup aggregation
- Query API with DSL parser
- Cost monitoring and alerts
- Web dashboard UI
- Semantic cache with prefix registry
- A/B testing framework for compression strategies
- Enterprise: compliance controls, policy engine, SLA monitoring, governance
- Enterprise: audit trail, DLP scanning
- License system with activation and validation
- Agentic workflows: budgets, failure memory, handoff, locks, retry, prefetch
- Session capsules for conversation compression
- Agent adapters: OpenClaw, Claude CLI, generic
- Tool schema registry for prompt-cache stability
- Docker support (Dockerfile + docker-compose)
- 10 example scripts covering common use cases
- 9 pre-built configuration profiles
- 5 deployment guides (Docker, AWS ECS, GCP Cloud Run, K8s, standalone)
- Comprehensive documentation (installation, configuration, CLI reference, architecture, security)

[1.0.0]: https://github.com/tokenpak/tokenpak/releases/tag/v1.0.0

