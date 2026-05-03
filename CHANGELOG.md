# Changelog

All notable changes to TokenPak are documented in this file.

This project follows [Semantic Versioning](https://semver.org/).

## [Unreleased] — install footprint extras split (TIP7-001)

### Changed — slim core install (Standard 02 §9 / Constitution 00)

The default `pip install tokenpak` no longer pulls torch / CUDA / scipy / pandas / litellm / llmlingua / sentence-transformers / tree-sitter-languages as hard dependencies. These have been moved to named optional extras under `[project.optional-dependencies]` so the slim core matches the public "no external dependencies for core functionality" claim.

**Migration — breaking install change for users who depend on the heavy features:**

| If you used… | Install with… |
|---|---|
| Vector retrieval / cross-encoder rerank (`tokenpak.retrieval.vector_local`, `tokenpak.compression.span_extractor`) | `pip install tokenpak[retrieval]` |
| Tree-sitter code-aware compression (`tokenpak.compression.processors.code_treesitter`) | `pip install tokenpak[code-compression]` |
| A/B optimizer significance tests (`tokenpak.intelligence.ab_optimizer`, scipy.stats) | `pip install tokenpak[intelligence]` |
| Pandas-based reporting | `pip install tokenpak[data]` |
| LLMLingua compression engine | `pip install tokenpak[compression]` |
| LiteLLM router middleware (`tokenpak.integrations.litellm.*`) | `pip install tokenpak[integrations-litellm]` |
| Everything bundled (legacy 1.5.0 behavior) | `pip install tokenpak[full]` |

Every guarded import site already raises `ImportError` with the install hint when a feature extra is missing — runtime behavior is unchanged for users who add the extra. CI and the dev workflow now use `pip install -e .[full,dev]`.

The new test `tests/test_dependencies_extras.py` enforces the slim-core invariant on every PR; it fails if any heavy package re-enters `[project.dependencies]` or any required extra is removed.

Source: `02_COMMAND_CENTER/proposals/2026-05-01-tokenpak-install-footprint-extras-split.md`.

## [1.5.0] - 2026-05-03

### Added (2026-04-28 — Phase 0, pmgtm v2)
- **Proxy-level auth gate (`TOKENPAK_PROXY_AUTH_TOKEN`)** — opt-in middleware in `tokenpak/proxy/proxy_auth.py`, wired into `_ProxyHandler` (`server.py`). Localhost stays trusted; non-localhost requests now require `Authorization: Bearer <token>` whenever the env var is set, else 403. `hmac.compare_digest` for timing-safe comparison; SHA-256 hex of the token populates the new `user_id` column on the SQLite `requests` table (canonical telemetry-row identity, written via `Monitor.log`) and `extra.user_id` on the structured JSON request log — the raw token is never logged. Schema migration is additive (`ALTER TABLE requests ADD COLUMN user_id TEXT DEFAULT ''`), back-compatible with pre-A6 rows. I5 header-allowlist enforced: the proxy auth Bearer is stripped before forwarding upstream so the upstream provider only ever sees its own `x-api-key`. Tests in `tests/proxy/test_proxy_auth.py` cover all four gating paths, the I5 invariant against an in-process mock upstream, and a SQLite read-back asserting the row's `user_id` is the hash and never the raw token. Docs at `docs/configuration/proxy-auth.md`. Prerequisite for any future Team-tier RBAC. (P0-06 / pmgtm-v2 / M-A6, AC-A6 — standards 02 §11, 03 CLI-UX, 21 §10)
- **Headline benchmark CI gate** (`tests/benchmarks/test_headline_claim.py`, `tests/fixtures/headline_corpus.txt`) — Deterministic 9-message DevOps agent corpus (~8 kB) and a blocking pytest assertion that compression stays in [30, 50]%. Run locally: `make benchmark-headline`. Blocking job in CI per standard 21 §9.8. (P0-05 / pmgtm-v2 / M-A5)

### Added (2026-04-17 — companion/proxy REST architecture)
- **Proxy `/tpk/v1/*` REST API** (`tokenpak/proxy/app_endpoints.py`) — nine endpoints for proxy-owned resources, distinct from the `/v1/*` LLM passthrough:
  - `GET /tpk/v1/health` — version, uptime, vault status
  - `GET /tpk/v1/vault/search?q=&limit=` — BM25 search over the vault
  - `GET /tpk/v1/vault/block/{block_id}` — full block content
  - `GET /tpk/v1/budget` — session + daily cost snapshot
  - `GET /tpk/v1/journal/sessions?limit=` — recent companion sessions
  - `GET /tpk/v1/journal/{session_id}?entry_type=&limit=` — journal entries
  - `POST /tpk/v1/journal/{session_id}/entry` — add journal entry
  - `POST /tpk/v1/compress` — head/tail truncate to max_tokens
  - `POST /tpk/v1/optimize` — offline prompt linter report
  - `POST /tpk/v1/tokens/estimate` — token count for text/file
  - `GET /tpk/v1/capsules`, `GET /tpk/v1/capsules/{id}` — memory capsules
  - `GET /tpk/v1/session/info` — proxy environment snapshot
  - Localhost-only auth by default; optional `X-TPK-Key` header if `TOKENPAK_PROXY_KEY` is set
- **Companion MCP tools now HTTP-call the proxy** — all 9 tools (vault_search, vault_retrieve, check_budget, journal_read, journal_write, prune_context, load_capsule, estimate_tokens, session_info) route through `/tpk/v1/*`. Single source of truth for state; no more duplicate VaultIndex / budget tracker.
- **`tokenpak integrate`** — GTM friction-kill: one command to point your LLM client at the proxy. Print-mode for all 9 supported clients (Claude Code, Cursor, Cline, Continue.dev, Aider, Codex CLI, OpenAI SDK, Anthropic SDK, LiteLLM). `--apply` mode writes configs for clients with stable config formats: Claude Code (`~/.claude/settings.json`), Cursor (platform-specific settings.json), Continue.dev (`~/.continue/config.json`), Aider (`~/.aider.conf.yml`). Always backs up before writing and prints a rollback command.
- **`tokenpak license` / `plan` / `activate` / `deactivate`** — Free-tier defaults today, Pro/Team/Enterprise surface ready. 52 gated features cataloged in `tokenpak/licensing/_GATES` (single `is_feature_enabled(name)` choke point). License stored at `~/.tokenpak/license.json`.
- **`tokenpak compress`** — real implementation (was a paywall stub): runs offline, detects JSON messages for dedup, supports `--file`, stdin, `--json`, `--verbose`.
- **`tokenpak optimize`** — real implementation (was a paywall stub): offline prompt linter reporting whitespace bloat, repeated phrases, verbose phrasings. `--file` / stdin / `--json` modes. Dispatches to session-level optimizer when no input is given.
- **1h `cache_control` TTL support in proxy** (`tokenpak/proxy/prompt_builder.py:_cache_control_dict()`) — read from `TOKENPAK_CACHE_TTL` env var. Set `1h` to emit `{"type":"ephemeral","ttl":"1h"}` on all cache_control markers, extending Anthropic's 5-min default to 1h. Worth the 2x write cost for cron traffic that fires at 30-min intervals.
- **Agent-cycle wiring** (`vault: 06_RUNTIME/scripts/agent-claude-worker.sh`) — cron cycles now route through the local tokenpak proxy (`ANTHROPIC_BASE_URL=http://127.0.0.1:8766`) AND attach the companion (mcp + settings + system-prompt). Overrides: `CYCLE_PROXY_BYPASS=1`, `CYCLE_COMPANION_BYPASS=1`, `TOKENPAK_PROXY_BASE_URL`.
- **Monitor SQLite writer re-wired** — `Monitor` class in `tokenpak/proxy/monitor.py` was orphaned after TPK-CONSOLIDATION (wrote to JSONL logs only, never SQLite). `ProxyServer.__init__` now instantiates `Monitor(~/.tokenpak/monitor.db)` and the request handler calls `.log()` after token parsing.
- **Companion hook budget-block writes `companion_savings`** (`tokenpak/companion/hooks/pre_send.py`) — when the budget gate blocks a request, the estimated tokens really are avoided; entry_type matches what `tokenpak status` Prompt-side plane expects.

### Changed (2026-04-17)
- **Auto-discover models instead of seed catalog only** (`tokenpak/models/_discovery.py`) — `auto_start_if_enabled()` now opts IN when an API key is present (was opt-in via `TOKENPAK_MODEL_DISCOVERY=1`). Family-rule inference in `_families.py` already handles unseen models (e.g. `claude-opus-4-7` → opus tier pricing) with no seed edit required.

### Changed
- **Compression now enabled by default** — `ENABLE_COMPACTION`, `BUDGET_CONTROLLER_ENABLED` default to `True`; `COMPACT_THRESHOLD_TOKENS` defaults to `1500` (was `4500`). To restore the legacy passthrough behavior, use `tokenpak serve --safe`. (TRIX-01 / pmgtm)

### Added
- **Claude Code client-auth pass-through** (`proxy.py`) — When Claude Code sends its own OAuth credentials (`Authorization: Bearer` + `anthropic-beta: oauth-2025-04-20`), the proxy preserves the original request bytes while applying response-side features (cost tracking, logging, budget enforcement). Byte preservation is required because JSON re-serialization changes the request signature, causing Anthropic's billing to route to the wrong quota pool (`YOU_RE_OUT_OF_EXTRA_USAGE`).
- **Byte-level vault injection** (`proxy.py`: `_find_system_array_close()`, `_byte_inject_system_block()`) — Splices vault context directly into the JSON system array at a byte offset without `json.loads`/`json.dumps` round-trip. Preserves all original bytes except the insertion point. Configurable via `TOKENPAK_CC_INJECT_MAX_CHARS` (default 2000) and relevance-gated via `TOKENPAK_CC_INJECT_MIN_QUERY` (default 50 chars).
- **Full header forwarding for Claude Code** (`proxy.py`) — Client-auth requests forward all headers verbatim (no allowlist filtering) to preserve the request identity Anthropic uses for OAuth quota routing. Includes `x-app`, `X-Stainless-*`, `Content-Type`, and all Anthropic beta flags.
- **TTL-aware cache_control in anthropic adapter** (`tokenpak/proxy/adapters/anthropic_adapter.py`: `_body_has_explicit_ttl()`) — `inject_system_context()` now detects requests with explicit `ttl` values in cache_control blocks and skips adding default ephemeral markers that would violate Anthropic's TTL ordering rule.
- **`inject_vault_context()` returns raw injection text** (`proxy.py`) — 4th tuple element enables byte-level injection to reuse vault search results without re-running the search.

### Fixed
- **Failover iterator thread safety** (`tokenpak/proxy/failover.py`) — `FailoverManager.iter_providers()` now snapshots the provider chain under a lock before iterating, preventing `RuntimeError: dictionary changed size during iteration` when `reload_config()` races with an in-flight iteration (TRIX-MTC-07 #1)
- **Circuit breaker config reload synchronization** (`tokenpak/proxy/circuit_breaker.py`) — Added `CircuitBreakerRegistry.reload_config()` which re-reads env vars and propagates the new config to all existing breakers under the registry lock, preventing stale-config races (TRIX-MTC-07 #2)
- **Streaming handler cross-chunk SSE buffering** (`tokenpak/proxy/streaming.py`) — `StreamHandler.process_chunk()` now accumulates text in a line buffer and flushes only complete lines into the byte buffer, preventing parse failures when a `data: {...}` SSE event spans two `recv()` calls (TRIX-MTC-07 #3)
- **Cost tracking failure audit trail** (`tokenpak/proxy/proxy.py`) — When `cost_tracker.record_request()` raises, the failure is now logged at `ERROR` level with a structured `COST_TRACKING_FAILURE model=... tokens=...` message instead of a bare `WARNING`, so ops dashboards can detect cost data loss (TRIX-MTC-07 #4)
- **Router Content-Length validation** (`tokenpak/proxy/router.py`) — `ProviderRouter.route()` now raises `ValueError` when the `Content-Length` header doesn't match the actual body size or is non-numeric, preventing truncated bodies from being silently forwarded upstream (TRIX-MTC-07 #5)
- **Passthrough config post-deserialization validation** (`tokenpak/proxy/passthrough.py`) — `PassthroughConfig.__post_init__()` now raises `ValueError` if any header name appears in both `strip_headers` and `safe_to_log`, catching contradictory configs at construction time rather than producing undefined forwarding behavior at runtime (TRIX-MTC-07 #6)

### Added
- **`tokenpak prune` command** — Top-level alias for `tokenpak audit prune`; accepts `--days` (retention window) and `--db` (audit DB path) flags
- **CLI surface consistency test** — `tests/cli/test_help_surface_consistency.py` asserts every command in `tokenpak --help` exits 0 on `<cmd> --help`
- **CrewAI adapter** (`tokenpak/adapters/crewai/`) — `TokenPakContext`, `TokenPakCrewAIHook`, `TokenPakCrew`, `TokenPakHandoff`; install with `pip install tokenpak[crewai]` (CALI-MTC-02)
- **AutoGen adapter** (`tokenpak/adapters/autogen/`) — `TokenPakConversationHook`, `TokenPakAssistant`, `TokenPakGroupChat`, `compress_messages`; install with `pip install tokenpak[autogen]` (CALI-MTC-02)
- **LlamaIndex adapter** (`tokenpak/adapters/llamaindex/`) — `TokenPakSynthesizer`, `TokenPakQueryEngine`, `TokenPakIndex`, `MultiIndexFusion`; install with `pip install tokenpak[llamaindex]` (CALI-MTC-02)
- `pyproject.toml` extras: `[crewai]`, `[autogen]`, `[llamaindex]` (CALI-MTC-02)

### Removed (with replacement)
<!-- CALI-MTC-01: CLI surface cleanup — 8 phantom commands resolved -->

| Removed phantom | Resolution | Canonical replacement |
|---|---|---|
| `tokenpak prune` | Implemented as top-level alias | `tokenpak audit prune` (same `--days`, `--db` flags) |
| `tokenpak list-models` | Removed from docs (was never in `--help`) | `tokenpak models` |
| `tokenpak provider-status` | Removed from docs (was never in `--help`) | `tokenpak status` or `tokenpak doctor` |
| `tokenpak provider-force-health` | Removed from docs (was never in `--help`) | `tokenpak doctor --fix` |
| `tokenpak rebuild-vault-index` | Removed from docs (was never in `--help`) | `tokenpak vault repair` |
| `tokenpak cache-stats` | Removed from docs (was never in `--help`) | `tokenpak stats` |
| `tokenpak list-keys` | Removed from docs (was never in `--help`) | No direct replacement — use provider dashboard |
| `tokenpak proxy --config` | Removed from docs (was never in `--help`) | `tokenpak start` with config at `~/.tokenpak/config.yaml` |

---

## [1.0.2] - 2026-03-25

### 🚀 OSS Launch
- Public OSS launch on GitHub with full CI pipeline.
- Migrated to pyproject.toml packaging standard.
- GitHub Actions CI with matrix testing (Python 3.10–3.13).

### Changed
- Version bumped from 1.0.1 to 1.0.2 for OSS launch.
- Updated packaging to pyproject.toml (replaces legacy setup.py).
- README badges updated to live CI status.

## [1.0.1] - 2026-03-18

### Changed
- Minor stability fixes and dependency updates.
- Improved error handling in fallback routing.

## [1.0.0] - 2026-03-10

### 🚀 Highlights
- Stable provider-agnostic routing across Anthropic, OpenAI, and compatibility paths.
- Layered fallback orchestration for improved reliability under model/provider failures.
- Compression and budgeting pipeline hardened for large-context workloads.

### Added
1. Added provider mirroring aliases for consistent model addressing.
2. Added interleaved fallback chains across provider groups.
3. Added routing controls for deterministic fallback behavior.
4. Added SDK Python recipe examples for common integration patterns.
5. Added expanded CLI surfaces for diagnostics and operator workflows.
6. Added capsule builder stages for compact context packaging.
7. Added Phase7 router integration for structured context handling.
8. Added budget controller hooks for token-limit enforcement.
9. Added dashboard support modules and monitoring endpoints.
10. Added improved integration test coverage for routing behavior.
11. Added docs for deployment, troubleshooting, and migration reference.
12. Added safer startup validation in service and proxy paths.

### Changed
13. Changed default operational model chains to prioritize resilient codex-compatible paths.
14. Changed fallback ordering to reduce hard-failure cascades.
15. Changed CLI behavior to surface richer diagnostics in failure modes.
16. Changed internal packaging flow to improve deterministic outputs.
17. Changed healthcheck behavior for clearer readiness signaling.
18. Changed docs structure to separate architecture, deployment, and operator runbooks.

### Fixed
19. Fixed double-proxy regressions introduced during self-repair iterations.
20. Fixed optional ValidationGate import handling to prevent startup breaks.
21. Fixed compression pipeline API mismatch (`compress()` -> `run()`).
22. Fixed service environment gaps that silently disabled router/capsule stages.
23. Fixed multiple edge-case path mappings for model alias resolution.
24. Fixed formatting/type consistency in newly added SDK examples.

### Performance
25. Improved large-context compaction efficiency in benchmarked runs.
26. Improved fallback recovery time under upstream rate-limit pressure.
27. Improved cache and dedup behavior in repeated prompt structures.

### Docs
28. Updated architecture docs for v1.0 routing and compression design.
29. Updated deployment checklist for current service wiring.
30. Updated troubleshooting docs for rate-limit and proxy-path diagnostics.

### Breaking Changes
- **Routing defaults updated:** custom wrappers that assumed legacy v0.x fallback order must update chain expectations.
- **Service env requirements tightened:** production startup now expects explicit router/capsule-related env toggles.
- **CLI output shape adjusted:** automation parsing CLI text output should use stable fields/options where available.

### Migration Notes (v0.x → v1.0)
- Review fallback chain configuration and map old aliases to new mirrored provider aliases.
- Ensure service environment defines router/capsule feature toggles explicitly.
- Re-run smoke tests for startup, health, routing, and fallback behavior.
- Validate any custom parsing around CLI output before deploying to production.

---

## [0.9.0] - 2026-02-20

### Added
- Provider-agnostic routing foundation with Anthropic and OpenAI adapter support.
- Vault index: semantic retrieval of compressed context blocks from local markdown vaults.
- Compression pipeline: salience-based extraction, dedup, and token budgeting.
- Telemetry server with SQLite-backed usage tracking.
- Docker image with multi-stage build and non-root runtime.

### Changed
- Migrated from single-file proxy to modular `tokenpak/` package structure.

### Fixed
- Streaming SSE passthrough race condition under concurrent requests.

---

## [0.5.0] - 2026-01-28

### Added
- Initial compression pipeline: document and code salience extractors.
- Vault block indexing with FAISS-backed retrieval (replaced with SQLite in v0.9).
- Basic CLI: `tokenpak serve`, `tokenpak status`, `tokenpak doctor`.
- WebSocket proxy endpoint (`/ws`) for real-time streaming clients.
- Benchmark suite for proxy passthrough, vault lookup, and routing decisions.

### Changed
- Moved from monolithic `proxy.py` to layered architecture (router → adapter → backend).

---

## [0.3.0] - 2026-01-10

### Added
- Core HTTPS proxy with pass-through to Anthropic Messages API.
- Token counting and budget enforcement hooks.
- Request/response logging with configurable verbosity.
- Initial recipe system for reusable compression configurations.

---

## [0.1.0] - 2025-12-20

### Added
- Initial prototype: HTTPS proxy rewriting requests to Anthropic Claude API.
- Proof-of-concept context compression reducing prompt tokens by ~30%.
- Basic configuration via YAML file.
- Single-file `proxy.py` implementation.
