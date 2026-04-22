# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.5] - 2026-04-22

### Fixed — release-gate hotfix-2

Recovers from a second burned tag attempt. v1.3.3 failed on the release-gate test step; v1.3.4 got past that but failed on (a) the `python -m tokenpak.cli --help` smoke invocation and (b) a Python 3.12-only tempdir cleanup race in the Layer A conformance test. v1.3.5 carries the same content as v1.3.3/v1.3.4 plus three narrowly-scoped fixes.

Both v1.3.3 and v1.3.4 are burned attempts retained on history for auditability. No PyPI publication occurred for either. Users should install v1.3.5 directly.

- **`release.yml` Smoke test CLI step** uses `tokenpak --help` (installed console-script entry point) instead of `python -m tokenpak.cli --help`. `tokenpak.cli` is a package without `__main__.py`, so `-m` execution fails; the entry point has always been the correct end-user invocation.
- **`tests/conformance/test_layer_a_pipeline.py`** — both `tempfile.TemporaryDirectory()` call sites use `ignore_cleanup_errors=True`. Monitor.log's async SQLite writer thread can still hold files in the tempdir when the test context exits; Python 3.12's `shutil.rmtree` surfaces this as `OSError` without the flag. The observer row (the thing the test asserts on) is captured synchronously before teardown; the disk artifact is incidental here.
- **`.github/workflows/release.yml`** gains a `workflow_dispatch:` trigger so the test + build jobs can be fired manually against any candidate commit before a real tag is cut. The `release` and `publish` jobs are guarded by `if: github.event_name == 'push'` so dispatch runs never create a GitHub Release or upload to PyPI. Intended use: `gh workflow run release.yml --ref <commit>` as a preflight before tagging.

### No scope expansion

No TIP-SC semantic changes. No broader workflow redesign. No unrelated cleanup. No runtime behavior changes.

## [1.3.4] - 2026-04-22

**Burned tag / failed release attempt — no PyPI publication, no GitHub Release page.** Failed at `Smoke test CLI` (`python -m tokenpak.cli` invocation bug) and at the Python 3.12 matrix leg of self-conformance (tempdir cleanup race). Tag retained on history for auditability. Fix lands in v1.3.5.

### Fixed — release-gate hotfix

Recovered the TIP-SC phase from a failed v1.3.3 release attempt (no PyPI publication; no GitHub Release page). The v1.3.3 tag is a burned attempt retained on history for auditability; v1.3.4 carries the same content plus the fix below.

- **`release.yml` test step** no longer runs the `tests/conformance/` tree (`--ignore=tests/conformance`). The conformance suite is the canonical job of `tip-self-conformance.yml` per DECISION-SC-08-1; duplicating it in the release-gate required a registry checkout + `TOKENPAK_REGISTRY_ROOT` wiring that workflow intentionally does not carry. The v1.3.3 release failed at this step because the conformance tests couldn't resolve registry schemas.
- **`tests/conformance/conftest.py::_discover_registry_root`** gains a 4th fallback to the vendored `tokenpak/_tip_schemas/` tree (via `importlib.resources`). Layer A + manifest + self-capability tests now run standalone in any installed env.
- **New helper `installed_validator_knows_schema(name)`** + module/test-level `pytest.mark.skipif` gates on Layer B + the Layer-C journal smoke. Tests that depend on schemas added after the pinned PyPI validator's release skip gracefully instead of failing (mirrors the SC-07 runner's WARN convention on the pytest side). The SC-08 CI path (registry-editable install) has every schema; the skip never fires there.

### No scope expansion

No TIP-SC semantic changes. No workflow redesign. No cleanup mixed in. Version-scheme retirement (SC-09) stays in effect: `1.3.3` → `1.3.4`.

## [1.3.3] - 2026-04-22

**Burned tag / failed release attempt — no PyPI publication, no GitHub Release page.** Content is identical to the v1.3.4 entry above (plus the release-gate hotfix). Tag retained on history for auditability.

### Added — TIP-1.0 self-conformance (Phase TIP-SC)

Mechanical proof that the reference implementation satisfies TIP-1.0 — live artifacts captured by a harness and validated against the registry schemas on every CI run. No paper-spec; no legacy restoration; centralized observer shared by proxy + companion.

- **ConformanceObserver + emission sinks** (`tokenpak/services/diagnostics/conformance/`) — single shared contract at five production chokepoints (`Monitor.log`, proxy response headers × 2 paths, `JournalStore.write_entry` + `pre_send._journal_write(_savings)`, boot-time capability publication for both profiles). No-op when no observer installed; ship-safe.
- **LoopbackProvider** — deterministic, network-free provider stub keyed by `RouteClass`. Gated by `TOKENPAK_PROVIDER_STUB=loopback`; production paths untouched when unset.
- **`tokenpak/manifests/{tokenpak-proxy,tokenpak-companion}.json`** — canonical TIP-1.0 self-declaration manifests shipped in the wheel. Capabilities arrays are one-to-one with `SELF_CAPABILITIES_*` (asserted in CI).
- **Conformance pytest suite** (`tests/conformance/`, 28 tests) across Layer A (pipeline + Monitor.log), Layer B (companion pre_send), Layer C (startup + disk-artifact round-trips). Full schema validation via `tokenpak-tip-validator`.
- **`tokenpak doctor --conformance`** (+ `--json`) — operator-facing CLI runner over the same primitives. Nine checks; explicit exit-code contract: 0 = OK, 1 = conformance failure, 2 = tooling error. Works from an installed wheel via vendored schemas at `tokenpak/_tip_schemas/schemas/`.
- **`.github/workflows/tip-self-conformance.yml`** — blocking/advisory split per standard 21 §9.8. Matrix on Python 3.10/3.11/3.12 for main/release/**/hotfix/**/v* tags + PRs to main|release/**. Advisory (single Python, `continue-on-error: true`) for every other branch. Process-enforced gating; reviewers honor `self-conformance (blocking) / 3.1{0,1,2}` status-check names as required.
- **Registry `companion-journal-row.schema.json`** + 3 conformance vectors (upstream in `tokenpak/registry`) so companion artifacts are schema-validatable.
- **Canonical capability refresh** — `SELF_CAPABILITIES_PROXY` 6 → 10, `SELF_CAPABILITIES_COMPANION` 4 → 7, matching what proxy + companion actually implement. Validator's catalog drift source fixed to read `capability-catalog.json` (previously drifted against embedded schema examples).

### Vendored-schema sync discipline

The tokenpak wheel ships a copy of the TIP schemas at `tokenpak/_tip_schemas/schemas/{tip,manifests}/` so `tokenpak doctor --conformance` works without a registry checkout. This is a **vendored mirror** of `tokenpak/registry:schemas/`. Any TIP-MINOR schema change touches three surfaces in order:

1. `tokenpak/registry:schemas/` — authoritative source.
2. `tokenpak-tip-validator` PyPI republish — `_SCHEMA_PATHS` map in sync with the new schemas.
3. `tokenpak:tokenpak/_tip_schemas/schemas/` — vendored copy refreshed.

See `tokenpak/_tip_schemas/README.md` for the sync checklist.

### Versioning

Bumps from `1.3.002` → `1.3.3` — returns to canonical 3-segment PEP 440 (`1.3.002` canonicalized to `1.3.2` on PyPI; `1.3.3` is the next clean slot). The 3-digit internal patch scheme (`1.2.091..095`, `1.3.001..002`) is retired as of this release.

## [1.3.002] - 2026-04-22

### Fixed
- **Active pre-send hook.** `companion/hooks/pre_send.py` now loads active session capsule + vault context and emits them via Claude Code's `hookSpecificOutput.additionalContext` — the only place tokenpak can add pre-wire value on byte-preserve routes. Each mutation records a `companion_savings` journal row per the 2026-04-17 attribution contract.
- **Default system prompt non-empty.** Instructs Claude Code's model to call `check_budget` / `prune_context` / `load_capsule` / `journal_*` MCP tools proactively. Overridable via `TOKENPAK_COMPANION_SYSTEM_PROMPT`.
- **`tokenpak status` Features row** — replaced phantom-key ❌s with live Policy state: `classifier ✅ | DLP warn | TTL-order ✅ | enrichment ❌ | compression ❌`. Honest reflection of actual 1.3.0 behavior for the active route.

### Added
- **`TokenPak (pre-wire)` status line.** Reads `companion_savings` rows and credits tokenpak for pre-wire work independent of platform cache activity.

### Opt-outs
- `TOKENPAK_COMPANION_ENRICH=0` — disable active enrichment.
- `TOKENPAK_COMPANION_MIN_QUERY_TOKENS` + `TOKENPAK_COMPANION_INJECT_BUDGET` — tune the relevance gate + budget.

## [1.3.001] - 2026-04-22

### Fixed
- **`tokenpak claude` MCP server no longer fails with "1 MCP server failed".** mcp.json invoked the server as `python3 -m tokenpak.companion.mcp_server` without the `-P` flag. Claude Code launches MCP servers with its own cwd (often the user's project dir); when that dir contained a sibling `tokenpak/` directory, Python resolved `import tokenpak` to the namespace directory and died with `ImportError: cannot import name '__version__' from 'tokenpak'`. Same class of bug the UserPromptSubmit hook hit in 1.2.7 — `-P` mitigation now applied to both spawn paths (hook and MCP server). Re-run `tokenpak claude` or `tokenpak integrate claude-code` to regenerate mcp.json.

## [1.3.0] - 2026-04-22

**Claude Code capability restoration complete.** All five approved phases (α β γ δ ε) live. Architecture: route classifier + Policy as the single branching signal; DLP + vault enrichment + backend selection + diagnostics + dashboard product surface all plug into the canonical pipeline stages. No duplicate proxy-vs-companion implementations. Legacy code was referenced as behavioral spec only; nothing was restored-as-is.

### Added — 1.3.0-ε (dashboard + inline savings + forecast)
- **`dashboard.panels.PerModePanel`** — groups monitor.db rows by endpoint family, returns JSON-serialisable data for web/API/CLI renderers. Handles missing db gracefully.
- **`alerts.inline_savings`** — `InlineSavingsEvent` + `build_event()` + `format_oneline()`. The TUI status-line renderer fits under Claude Code's input prompt.
- **`services.telemetry_service.forecast`** — linear extrapolation over the last N days; reports 7-day + 30-day projections with a rising/flat/falling trend heuristic.

### What 1.3.0 delivers end-to-end

1. **`RouteClass` taxonomy** with 9 values + `Policy` dataclass (α). Single source of truth consumed by proxy, companion, CLI, dashboard.
2. **Policy-gated pipeline** (β): DLPStage + ContextEnrichmentStage plug into the canonical `security` and `routing` slots. Byte-preserve protection mechanically enforced.
3. **Backend selection** (γ): `X-TokenPak-Backend: claude-code` header delegation to OAuth path; SDK routes to the default API path. No silent fallback.
4. **Adoption surfaces** (δ): `tokenpak integrate claude-code` one-shot, `tokenpak doctor --claude-code`, drift detector that catches the dist-info-shadow bug class.
5. **Product surface** (ε): per-mode dashboard panel + inline savings events + cost forecast.

### Tests
- 88 new tests total across α–ε (30 α + 32 β + 14 γ + 8 δ + 17 ε — some with shared conftest).
- 415 pass, 1 skipped, 1 xfailed, 10 deselected (pre-existing HTTP integration tests). Zero regressions over the 1.2.x baseline.

### Architecture enforcement
- `.importlinter`: 5/5 contracts KEPT at every phase. §5.2-C allowlist entries for the two valid entrypoint→services imports (companion classifier, CLI diagnostics).
- `tip-check`: 4/4 PASS.
- No ad-hoc `"claude-code" in …` branches outside `RouteClassifier`. Enforced by code review discipline, visible in the diff.

### Migration notes
- **No user-visible breaking changes.** Every existing 1.2.x flow continues to behave identically. Default policies keep new stages as no-ops on Claude Code routes until operators opt in.
- **New env overrides:** `TOKENPAK_POLICY_<FIELD>` for per-field policy tuning; typos are silently ignored so mis-configured env can't create phantom fields.
- **Public API additions:** `tokenpak.core.routing.{RouteClass, Policy}`; `tokenpak.services.routing_service.{classifier, backend_selector}`; `tokenpak.services.policy_service.{resolver, dlp_stage}`; `tokenpak.services.diagnostics.*`; `tokenpak.alerts.inline_savings.*`; `tokenpak.dashboard.panels.PerModePanel`; `tokenpak.services.telemetry_service.forecast`.

## [1.2.095] - 2026-04-22

### Added — 1.3.0-δ (integrate + doctor --claude-code + drift detector)
- **`tokenpak integrate claude-code`** — one-command setup via new public `companion.launcher.regenerate_config()`.
- **`tokenpak doctor --claude-code`** — delegates to shared `services.diagnostics` (core + CC check suites).
- **`services.diagnostics`** — `CheckResult`/`CheckStatus` + `run_core_checks()` + `run_claude_code_checks()` + `detect_install_drift()`. Catches the dist-info-shadow class of bug.
- **`companion.launcher.regenerate_config()`** — public API; CLI stops reaching into `_impl` internals.

### Tests
- 8 new tests. 398/0 fail baseline (was 390 at γ; zero regressions).

## [1.2.094] - 2026-04-22

### Added — 1.3.0-γ (platform + backend selector + OAuth backend)
- **`services.routing_service.platform`** — canonical `detect_platform()` + `detect_platform_name()`; legacy `agent/adapters/registry` remains as deprecation shim.
- **`services.routing_service.backends`** — Backend protocol + `AnthropicAPIBackend` (httpx) + `AnthropicOAuthBackend` (shells out to `claude` CLI for OAuth billing; 502/504 on unavailable — never silently falls back to API-key path).
- **`services.routing_service.backend_selector.BackendSelector`** — `X-TokenPak-Backend` header overrides route default; Claude Code routes default to OAuth.

### Tests
- 14 new tests. 390/0 fail baseline (was 376 at β; zero regressions).

## [1.2.093] - 2026-04-22

### Added — 1.3.0-β (DLP + context enrichment)
Two pipeline stages plug into the α foundation. Both are policy-gated — default preset policies keep them no-ops for Claude Code routes (byte_preserve + injection_enabled=false) until opted in.

- **`tokenpak.security.dlp`** — single-implementation outbound secret scanner:
  - `scanner.py` — `DLPScanner.scan()` + `scan_bytes()`. Stateless, safe to share.
  - `rules.py` — 11 default rules: AWS access+secret, Stripe live+restricted, GitHub PAT + fine-grained, OpenAI (+ project keys), Anthropic, Google, Slack, PEM private keys.
  - `modes.py` — `apply_mode(mode, body, findings)` → `DLPOutcome` with immutable decision. Modes: `off`, `warn` (log only), `redact` (inline rewrite), `block` (short-circuit). Unknown modes fail-open to `off`.
  - `Finding.redacted()` — safe-to-log representation; never leaks the matched secret into logs.
- **`services.policy_service.dlp_stage.DLPStage`** — request-pipeline Stage slot `security`. Reads `ctx.policy.dlp_mode`. Automatic `redact` → `warn` downgrade on `byte_preserve` routes so Claude Code OAuth billing contract is preserved even if operator mis-sets the policy. Short-circuits pipeline + sets `ctx.response` on `block`.
- **`services.routing_service.context_enrichment.ContextEnrichmentStage`** — request-pipeline Stage slot `routing`. Gated by `ctx.policy.injection_enabled` AND `ctx.policy.body_handling == "mutate"`. Enforces `injection_budget_chars` (truncates concatenated hits) and `injection_min_query_tokens` (relevance gate skips trivial prompts). Appends vault context as a non-cached block after existing `system` content. Retriever is a dependency (testable); default falls back to `tokenpak.vault.blocks.BlockStore.default()` when available.

### Architectural guarantees enforced in β
- **No duplicate DLP implementation.** Same `DLPScanner` is consumed by the Stage (proxy-side) and is available to companion's pre-send hook (§5.2-C local helper) for TUI-level warnings. Single rule registry.
- **No body mutation on byte-preserve routes.** Stage-level policy gate makes it impossible for a mis-configured policy or a surprise rule change to mutate a Claude Code request body.
- **Redact-to-warn downgrade visible in telemetry.** When the Stage protects byte-preserve by overriding policy, `stage_telemetry["security"]["dlp_mode_downgraded"]` records it — no silent mode changes.

### Tests
- 32 new tests: 11 scanner, 7 modes, 7 DLPStage integration, 7 ContextEnrichmentStage integration.
- Baseline: 376/0 fail (was 344 at α; +32 new, zero regressions).

### Import contracts + TIP
- `.importlinter` 5/5 KEPT. No new allowlist entries needed — DLP and enrichment both live under `security/` and `services/` respectively, which are valid dependencies for the pipeline.
- `tip-check` 4/4 PASS.

### Next
- **1.2.094 (1.3.0-γ):** profile presets wired to dashboard telemetry + platform auto-detection + `X-TokenPak-Backend: claude-code` delegation to OAuth backend.

## [1.2.092] - 2026-04-22

### Added — 1.3.0-α (foundation)
First phase of the approved 1.3.0 Claude Code capability restoration. Plumbing only — no user-facing behavior changes yet; every existing flow behaves identically. Lays the architectural base so β–ε can plug in cleanly.

- **`tokenpak.core.routing`** — canonical protocol primitives:
  - `RouteClass` enum (9 values): `claude-code-{tui,cli,tmux,sdk,ide,cron}`, `anthropic-sdk`, `openai-sdk`, `generic`.
  - `Policy` dataclass: typed per-request capability flags (`body_handling`, `cache_ownership`, `injection_enabled` + `injection_budget_chars` + `injection_min_query_tokens`, `dlp_mode`, `compression_eligible`, `ttl_ordering_enforcement`, `profile`, `capture_session_id_header`, `extras`).
- **`tokenpak.services.routing_service.classifier.RouteClassifier`** — the single authoritative classifier. Inputs: `Request` (headers + body fingerprint) or env markers. Outputs: `RouteClass`. Never raises. No other subsystem re-implements route detection.
- **`tokenpak.services.policy_service.resolver.PolicyResolver`** + 9 YAML preset files (`presets/*.yaml`) — loads `RouteClass`→`Policy` mapping at import time. Env overrides via `TOKENPAK_POLICY_<FIELD>` (only rewrites typed fields — typos are ignored, not silently accepted).
- **`tokenpak.services.request_pipeline.classify_stage.ClassifyStage`** — first-in-pipeline Stage that attaches `route_class` + `policy` onto `PipelineContext`. Also extracts the session-id header named by the Policy (e.g. `x-claude-code-session-id`) onto `Request.metadata["session_id"]`.
- **`PipelineContext`** (extend) gains `route_class: RouteClass | None` and `policy: Policy | None` fields. Every future stage branches on `ctx.policy.<flag>`, not on `ctx.route_class` directly.

### Refactored — existing 1.2.x behavior retrofitted onto the α architecture
- **`proxy/server.py` attribution** — the inline `cache_origin` heuristic (1.2.8) now derives from `Policy.cache_ownership`. For client-owned routes (all `claude-code-*`) origin is `client` immediately; for proxy-owned routes (`anthropic-sdk`) origin starts `unknown` and promotes to `proxy` only when the request_hook actually mutates the body. "Never over-claim" invariant preserved.
- **`companion/hooks/pre_send.py`** — restored in 1.2.9 as a full token-estimate + cost-preview + budget-gate + journal pipeline; now calls `RouteClassifier.classify_from_env()` and tags the canonical `route_class` onto every journal row. Single source of truth for the "is this Claude Code?" question across proxy + companion.

### Tests
- 30 new tests under `tests/services/routing/` and `tests/services/policy/`. 344/0 baseline unchanged.

### Import contracts
- 5/5 `.importlinter` KEPT. Added allowlist entries for the new proxy→services classify flow + the §5.2-C marker on `companion/hooks/pre_send.py` for its classifier import.

### Next
- **1.2.093 (1.3.0-β):** DLP scanner + context-enrichment Stage, both gated by `Policy.dlp_mode` + `Policy.injection_enabled`. Default policies keep β a no-op until explicitly opted in.

## [1.2.091] - 2026-04-22

### Changed
- **Versioning scheme** — patch iterations now use 3-digit zero-padded sub-patches (`1.2.091`, `1.2.092`, ...) instead of `1.2.10`, `1.2.11`. Better sort display and reserves `1.3.0` for the next approved major-implementation release (PEP 440 normalizes `1.2.091` → `1.2.91` on PyPI, so resolver ordering is unchanged: `1.2.9 < 1.2.91 < 1.2.92 < … < 1.3.0`).

### Audit
- **Claude Code integration gap audit** landed at `vault/02_COMMAND_CENTER/audits/2026-04-22-claude-code-integration-gap-audit.md`. Documents **9 memory-referenced features still missing** from the current tree (route classifier, policy table, vault bridge, DLP scanner, 6 `claude-code-*` profile presets, `X-TokenPak-Backend` delegation header, `x-claude-code-session-id` capture, `TOKENPAK_CC_INJECT_*` budget gate, per-host drift detector), plus ~14 unshipped tasks from the 2026-04-08 21-task CCI initiative. No feature code restored in this release — restoration is 1.3.0 scope pending approval.

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

