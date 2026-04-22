# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.9] - 2026-04-22

### Fixed
- **Companion pre-send hook was a skeleton — restored the full pipeline.** `tokenpak/companion/hooks/pre_send.py` had been labelled "Wave 1 skeleton" (it only logged a line to stderr and returned 0). Every feature that used to run on `UserPromptSubmit` for `tokenpak claude` was silently no-op. Restored the pipeline from commit `e5b248e67a`:
  1. **Token estimate** — transcript file-size + prompt text, char // 4 (kept stdlib-only so the hook stays <100ms).
  2. **Cost estimate** — per-model input rate (opus $15 / sonnet $3 / haiku $0.80 per 1M tokens) read from `TOKENPAK_COMPANION_MODEL`.
  3. **Budget gate** — if `TOKENPAK_COMPANION_BUDGET` is set and the projected total exceeds it, the hook returns exit 2 + `hookSpecificOutput.decision="block"` JSON so Claude Code blocks the send.
  4. **Journal write** — every cycle appends to `~/.tokenpak/companion/journal.db` (schema: `entries(session_id, timestamp, entry_type, content, metadata_json)`) so cross-session analytics, `session_info` MCP tool, and the status fleet-savings reader all see the activity.
  5. **Daily-cost accumulator** — appends to `~/.tokenpak/companion/budget.db`.
  6. **TUI status line** — stderr one-liner shown under the Claude Code input: `tokenpak: ~11 tokens  est $0.0000`.
- Accepts both the current `prompt` field and the Wave-1 legacy `message` field so older settings.json files still work.
- Works in both TUI and `--print` / cron modes (UserPromptSubmit fires in both since Claude Code 2.1.104).

### Note
- The MCP tools (`estimate_tokens`, `check_budget`, `prune_context`, `load_capsule`, `journal_read`, `journal_write`, `session_info`) were already registered — Claude Code can invoke them at any point during a session. The hook restoration makes the **automatic** per-prompt pipeline work again.

## [1.2.8] - 2026-04-22

### Fixed
- **`tokenpak status` now honestly attributes cache hits.** Per the 2026-04-17 attribution contract, cache activity where the client (Claude Code, Anthropic SDK, etc.) placed its own `cache_control` blocks is **platform/client-managed caching** — tokenpak provides no value for those hits and should never claim credit. Status previously merged all cache reads into a single "Cache hit rate" line regardless of who placed the markers, which over-credited tokenpak for `tokenpak claude` sessions where Claude Code owns the entire cache_control surface.

  The proxy now classifies every forwarded request by `cache_origin`:
  - `client` — request body already had `cache_control` blocks before the proxy touched it (upstream/platform manages the cache)
  - `proxy` — the proxy's `request_hook` / `apply_deterministic_cache_breakpoints` added the cache_control blocks (tokenpak manages the cache)
  - `unknown` — no cache_control activity anywhere (shouldn't happen often)

  Session counters now track `cache_requests_by_origin`, `cache_hits_by_origin`, and `cache_reads_by_origin` as separate dicts. `/stats::session` exposes them. `tokenpak status` renders the split:

  ```
  TokenPak cache:  52% (17 hits / 33 requests)  (48,910 tokens)
  Platform cache:  89% (41 hits / 46 requests)  (412,003 tokens, not credited)
  ```

  Never over-claims: if the classifier can't tell, the request lands in the `unknown` bucket and is explicitly marked "not credited."

## [1.2.7] - 2026-04-22

### Fixed
- **`tokenpak claude` UserPromptSubmit hook no longer fails** with `ImportError: cannot import name '__version__' from 'tokenpak'`. Root cause: Claude Code ran the hook with `cwd=/home/<user>`, which caused Python to interpret the sibling `tokenpak/` directory (project repo root) as a namespace package that shadows the editable install. The companion launcher's `settings.json` hook now uses `python3 -P -m …` (Python 3.11+ flag that suppresses cwd from sys.path), so the editable finder resolves first. Re-run `tokenpak claude` to regenerate the hook config; no other action needed.
- **Proxy enforces Anthropic's `cache_control` TTL-ordering rule.** Anthropic returns HTTP 400 when a `ttl='1h'` block appears after a default-ttl (`5m`) block in document order (tools → system → messages.content). The proxy's own deterministic-breakpoint pass was adding 5m markers to the last stable system block, while clients like Claude Code place 1h markers in `messages[].content[]`. Added `enforce_ttl_ordering(body)` as a final pass that walks the full document, finds the last explicit-TTL position, and strips every default-TTL `cache_control` that precedes it. No-op when no explicit-TTL blocks are present.

## [1.2.6] - 2026-04-22

### Fixed
- **Proxy no longer causes `Decompression error: ZlibError` in clients** (Claude Code, Anthropic SDK, OpenAI SDK). httpx's `.content` and `iter_bytes()` auto-decompress gzipped upstream responses, but the proxy was forwarding upstream's `Content-Encoding: gzip` header unchanged. Clients saw the gzip label on plaintext bytes and failed zlib inflation. Now strips `content-encoding` along with the other hop-by-hop headers on both the streaming and non-streaming response paths. Clients decompress (or don't) correctly based on the actual body they receive.

## [1.2.5] - 2026-04-21

### Fixed
- **`tokenpak status` now reflects live counters.** Previously read `health["stats"]` (a non-existent nested key in the current /health schema), so every counter displayed 0 even when the proxy had served hundreds of requests. Now reads from `/stats` (authoritative session state) with `/health` top-level fields as fallback. `Compilation:` also now sources from `/stats::compilation_mode` and shows the actual mode (e.g. `hybrid`) instead of `unknown`.
- **Proxy counts forwarded requests immediately**, not only after successful token-extraction. The in-memory `ps.session["requests"]` (and `errors` for 4xx/5xx) now increments at the top of the request-logging block, so `/health::requests_total` stays honest even on upstream error responses or when body parsing fails.

## [1.2.4] - 2026-04-21

### Fixed
- **`tokenpak claude` now actually routes traffic through the local proxy.** The companion launcher was exec-ing `claude` without setting `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL`, so Claude Code's API calls bypassed the proxy entirely. Result: `tokenpak status` showed zero requests, `monitor.db` stayed empty, and every other dashboard was frozen even while Claude Code was active. The launcher now sets `ANTHROPIC_BASE_URL=http://127.0.0.1:$TOKENPAK_PORT` and `OPENAI_BASE_URL=…/v1` in the child process env (respects pre-existing values — does not clobber). Set `TOKENPAK_PROXY_BYPASS=1` to disable.

## [1.2.3] - 2026-04-21

### Fixed
- **Telemetry is no longer silent.** The proxy now writes every completed request to `~/.tokenpak/monitor.db` again. A prior refactor orphaned the Monitor writer — the class was deleted from `tokenpak/proxy/monitor.py` and the `.log()` call sites in `tokenpak/proxy/server.py` were removed. `tokenpak status`, `tokenpak cost`, `tokenpak savings`, and the dashboards have all been reading from an ever-staler `monitor.db` since 2026-04-19. This release restores the Monitor class + re-wires it into `ProxyServer.__init__` and the per-request post-parse path. Async write queue (<0.1ms enqueue), fail-open (DB errors never break the request).
- **Hook import regression surfaced by the live audit.** The fleet had a stale `tokenpak-1.1.0.dist-info` finder left behind by an older `pip install -e`; that finder was being picked up intermittently by `/usr/bin/python3 -m tokenpak.companion.hooks.pre_send` and produced `ImportError: cannot import name '__version__' from 'tokenpak'` inside Claude Code's `UserPromptSubmit` hook. Re-installing 1.2.3 on top of a stale 1.1.x cleans the old finder out.

### Note
- If you're upgrading from 1.2.0/1.2.1/1.2.2, your proxy will start logging again on the next restart. Existing `monitor.db` rows pre-1.2.0 are preserved; there's no migration.

## [1.2.2] - 2026-04-21

### Fixed
- **`tokenpak dashboard` no longer crashes** with `ModuleNotFoundError: tokenpak.token_manager`. The 32-char hex token generation + storage is inlined into `cmd_dashboard` now (prior refactor removed `token_manager.py` but left the import).
- **`tokenpak status` no longer crashes** when the proxy is running. The circuit-breakers section now iterates `circuit_breakers["providers"]` (the actual shape returned by `/health`) instead of calling `.get()` on the top-level `{enabled, any_open, providers}` dict which would `AttributeError` on the bool values.
- **`tokenpak start` no longer lies about success.** Child stderr now writes to `~/.tokenpak/proxy-stderr.log` (rotated at 256 KB), the health check polls `/health` for 10 seconds at 500ms cadence, and if the child exits early the CLI prints the tail of its stderr and exits non-zero. Previously the command said "Proxy launched — waiting for startup..." even when the daemon died milliseconds after spawn.
- **`tokenpak demo` no longer crashes** when the `recipes/oss/` data directory is absent from the installed package. The live-compression demo path runs without the recipe catalog; recipe-catalog paths print a friendly install-hint message and exit 0 instead of dumping a traceback.
- **`tokenpak benchmark` no longer crashes** with `ModuleNotFoundError: tokenpak.debug.agent`. Fixed a stale relative import in `tokenpak/debug/benchmark.py` — uses the absolute path `tokenpak.agent.compression.recipes` now.
- **`tokenpak index` with no args** no longer dumps an `argparse.ArgumentError` traceback. Prints a usage hint and exits 2.
- **Every output header now shows the real CLI version** (`TOKENPAK v1.2.2  | Status`) instead of a hardcoded `TOKENPAK v0.3.1`. `formatter.py` reads from `tokenpak.__version__`.

### Added
- **`tokenpak/proxy/__main__.py`** — so `python3 -m tokenpak.proxy` works. Existing systemd units (including the fleet's `tokenpak-proxy.service`) have been running `python3 -m tokenpak.proxy` with no `__main__.py` present, producing a 20k+ crash-loop counter. Now launches via `start_proxy(host=TOKENPAK_BIND, port=TOKENPAK_PORT, blocking=True)` by default.

### Note
- 1.2.0 and 1.2.1 had the same set of underlying bugs; 1.2.2 is the first release where the full CLI surface has been exercised end-to-end against a running proxy. Upgrading strongly recommended.

## [1.2.1] - 2026-04-21

### Fixed
- **Wired the `tokenpak install-tier` subcommand into the CLI dispatcher.** The module shipped in 1.2.0 but was never registered on the argparse tree, so `tokenpak install-tier pro` returned "Unknown command". The entire OSS→paid upgrade path documented in the 1.2.0 release notes now works as documented.
- **`tokenpak audit {list,export,verify,prune,summary}` + `tokenpak compliance report`** no longer crash with `ImportError` on OSS installs. These subcommands still exist (argparse help text is preserved) but now route to an Enterprise upgrade stub that prints the `install-tier enterprise` hint and exits 2. The real implementations live in `tokenpak-paid`.
- **Removed a fleet-internal Tailscale IP** (`100.80.241.118`) that was shipped as the default value for `upstream.ollama` in `tokenpak/core/config/loader.py`. Default is now the documented Ollama local port (`http://localhost:11434`). Users who override the value via `TOKENPAK_OLLAMA_UPSTREAM` or the config file are unaffected.
- **`tokenpak savings` no longer dumps a traceback** on fresh installs where the telemetry tables haven't been created yet. Now prints a friendly "no data yet — start the proxy and send some requests" message and exits 0.

### Note
- 1.2.0 was yanked on PyPI because of the 4 issues above, which were caught by a release-blocking audit before the 1.2.0 announcement went out. The tier-package separation itself (TPS-11) is unchanged in 1.2.1 — same wheel contents, same paid-package contract, same 3-layer gating. If you installed 1.2.0, upgrade to 1.2.1 with `pip install --upgrade tokenpak`.

## [1.2.0] - 2026-04-21 [YANKED]

### Changed
- **BREAKING** — Paid tier commands (Pro/Team/Enterprise) split out into the separate `tokenpak-paid` private package. The OSS `tokenpak` package now contains upgrade stubs for 25 command modules; real implementations ship separately and are installed via `tokenpak install-tier <tier>`. Tier-package-separation initiative (`2026-04-21`).
- **Removed ~5,150 LOC of paid implementation** from the OSS package: `cli/commands/{optimize, route, compression, diff, prune, retain, fingerprint, replay, debug, last, template, dashboard, savings, metrics, budget, serve, trigger, exec, workflow, handoff, compliance, sla, maintenance, policy, vault}.py` now emit a `DeprecationWarning` on import and print an upgrade message on invocation (exit 2). Every public symbol external callers imported is preserved, aliased to the same `_upgrade_stub`.
- **`tokenpak.enterprise.*`** (audit, compliance, governance, policy, sla) — canonical home moved to `tokenpak_paid.enterprise.*`. The OSS namespace now raises `ImportError` on attribute access (these modules exposed classes — a silent no-op stub would be surprising).
- **`tokenpak/cli/trigger_cmd.py`** reduced to a re-export shim pointing at the (stubbed) `tokenpak.cli.commands.trigger`.

### Added
- **`tokenpak.cli._plugin_loader`** — plugin discovery via `tokenpak.commands` entry-points. Paid commands installed via `tokenpak-paid` surface automatically. Feature-flagged via `TOKENPAK_ENABLE_PLUGINS=1` until the default flips in a future release.
- **`tokenpak install-tier <tier>`** subcommand — pip-installs the private `tokenpak-paid` package from `pypi.tokenpak.ai/simple/` using a local license key for HTTP Basic auth (`__token__:<KEY>`).
- **3-layer gating model** — (1) license-key-gated PEP 503 index controls package access, (2) `tokenpak_paid.entitlements.gate_command` runtime-gates every paid command based on tier + features, (3) license-server periodic revalidation with tier-dependent grace periods (14d Pro / 7d Team / 3d Enterprise) + 30d offline tolerance.

### Migration
- OSS users who previously ran `tokenpak optimize`, `tokenpak dashboard`, etc. see an upgrade message + non-zero exit. Path forward: `tokenpak activate <KEY>` → `tokenpak install-tier pro` (or `team`/`enterprise`). No code change required for users who only use OSS commands (`status`, `doctor`, `config`, `index`, etc.).
- `.importlinter` contract updated — dropped the obsolete `cli.commands.fingerprint → compression.fingerprinting.*` ignore entry (stub has no fingerprinting imports). 5/5 contracts still KEPT.

### Removed
- Direct access to paid implementation bodies from the OSS package (see migration note).

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

