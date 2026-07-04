# Changelog

All notable changes to TokenPak are documented in this file.

This project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.10.2] — 2026-07-03

> **Release note:** version **1.10.1 was never released.** Its release pipeline runs stopped
> fail-closed at the public-API snapshot gate (no build, GitHub Release, or PyPI artifact was
> produced) and the `v1.10.1` tag was retired. 1.10.2 carries the intended 1.10.1 changes
> below, plus the corrected public-API snapshot.

### Fixed
- **Proxy upstream transport reliability.** Transient upstream failures (connection resets,
  server disconnects, retryable 5xx honoring `Retry-After`) are now retried with a bounded,
  policy-driven recovery before any bytes reach the client — and never after streaming output
  has started. Failed-request recovery metadata is persisted with credentials redacted.
- **Connection pool no longer hands retries a dead connection.** A pooled client that raises a
  transport error is evicted (identity-checked) so the retry gets a fresh connection; evicted,
  idle-reaped, and LRU-displaced clients are retired and closed after a grace period instead of
  being closed while requests are still in flight on them. New pool metrics `evicted_clients`
  and `retired_pending_close`; pool timeouts are env-tunable via `TOKENPAK_POOL_CONNECT_TIMEOUT`
  and `TOKENPAK_POOL_READ_TIMEOUT`.

### Deprecated
- `tokenpak.proxy.server.MAX_UPSTREAM_RETRIES` is retained as a compatibility alias so existing
  imports continue to work, but it is now non-authoritative and deprecated (planned for removal
  in a future minor release): retry behavior is governed by `UpstreamRetryPolicy`, and
  `TOKENPAK_UPSTREAM_RETRIES` remains the supported operator control. Operator-facing behavior
  is unchanged.

### Release integrity
- **Public-API snapshot regenerated in the canonical release environment.** The previous snapshot
  had been regenerated against a stale installed package instead of the source tree, which is what
  stopped the 1.10.1 release runs at the snapshot gate. The snapshot now records the bounded-retry
  public surface (`tokenpak.proxy.upstream_retry`, re-exported by `tokenpak.proxy.server` and
  `tokenpak.proxy.server_async`) and drops two symbol records that were never part of the released
  package, plus a host-specific import-error record.

## [1.10.0] — 2026-06-28

### Added
- **TokenPak Dispatch graduates from preview to a released feature.** The `tokenpak dispatch`
  command (intake/routing, Decision Inbox, run-ledger lifecycle, observability) now ships in the
  released `pip install tokenpak` package — the Dispatch engine, its registry/schema data, and the
  user guide are included in the wheel. Delivery/receipt remain an explicit post-alpha preview
  (no live station execution wired yet).

### Fixed
- Importing the package no longer requires FastAPI: `tokenpak savings` (and other core value
  commands) work on a base install without the optional serve/dashboard extra.
- The CLI proxy-version probe now derives the expected version from the package version and reads
  `/health`, instead of a hard-coded value.

### Packaging
- Release wheels now include `budget_config.yaml` and `term_cards.json`; a build-time assertion
  verifies the Dispatch registry/schema data ships.


### Added

- **cli:** `tokenpak upgrade` opens the public Pro upgrade page, supports
  `--print-url` for non-interactive use, and honors `TOKENPAK_UPGRADE_URL`.

### Changed

- **license/status:** Free-tier upgrade guidance now points to
  `https://tokenpak.ai/pro`.

## [1.9.3] — 2026-06-22

Security patch: path-safety hardening for `pak` install and a default-deny CORS
policy on the proxy's content routes. Additive; one behavior change noted below.

### Security
- **pak install:** added a path-traversal guard (archive entries are resolved and
  confirmed within the target directory), symlinked entries are skipped during
  extraction, and checksum-verified messaging is now honest about what was checked.
- **proxy CORS:** the `/tpk/v1/*` JSON routes no longer emit
  `Access-Control-Allow-Origin: *`. CORS is now **default-deny** with an
  exact-origin allowlist.

### Changed
- **proxy CORS (behavior change):** a browser app fetching `/tpk/v1/*` from a
  different origin must now set `TOKENPAK_PROXY_CORS_ORIGINS` (comma-separated
  exact origins). A matching request `Origin` is echoed back with `Vary: Origin`,
  never `*`. CLI / SDK / MCP clients are unaffected — CORS applies to browsers only.

## [1.9.1] — 2026-06-16

Patch release: privacy/security hardening, honest telemetry, license alignment,
and a dispatch reliability fix. Additive; no breaking changes.

### Security & privacy
- **spend-guard:** credential headers (authorization, api-key, cookie, …) are
  never persisted to disk in the spend-guard pending/replay path.

### Added — telemetry
- **telemetry:** honest platform-origin attribution + a coverage metric surfaced
  in `tokenpak doctor`; session/agent/cycle ids threaded into the monitor.db
  write path for accurate per-source accounting.

### Fixed — dispatch
- **dispatch:** delivered dispatch runs now persist a receipt (receipt builder
  wired into the fulfillment flow); dispatch registry/schema files ship in the wheel.

### Docs
- **license:** package READMEs + PYPI_READINESS were previously corrected from `MIT` to
  `Apache-2.0` to match the canonical Apache-2.0 LICENSE.

## [1.9.0] — 2026-06-14

Minor release: a guided **onboarding & lifecycle** pass on the CLI. Additive
to existing behavior; no breaking changes to current workflows.

### Added — onboarding & lifecycle

- **cli:** `tokenpak uninstall` with `--soft` (remove TokenPak config/state,
  leave integrations) and `--hard` (full removal of TokenPak-owned state).
- **cli (doctor):** `tokenpak doctor --lifecycle` summary plus a default
  route-state line so the proxy/route status is visible at a glance.
- **cli (permissions):** permission-tier system for `tokenpak integrate`
  (strict / standard / auto, plus a multi-instance fleet tier) with a new
  `tokenpak permissions` verb, doctor rows, and an opt-in multi-instance fleet
  launcher mode — see `tokenpak permissions --help`.
- **config:** read-only `tokenpak config doctor` diagnostics and a
  `tokenpak config env` provenance view (loaded env vars + precedence), with a
  documented config load-order spec.
- **cli (integrate):** guided cross-shell `tokenpak integrate` flow with
  `--apply` / `--revert`, shell detection, and a `--no-tui` escape hatch for
  non-interactive use.
- **cli (menu):** hardened interactive menu renderer (single alternate-screen
  session, per-command lifecycle, cached non-blocking status strip; honest
  unknown metrics render as a dash, never a fabricated `$0.00`).

### Added

- TokenPak Dispatch (v0.1-alpha, **preview**) — scoped, station-based,
  resumable work packages with a Decision Inbox and delivery receipts.
  Available on the main branch only; **not yet included in a released
  `pip install tokenpak` package**. See `tokenpak dispatch --help`.

### Breaking — install footprint: heavy extras are now opt-in

**Background:** `pip install tokenpak` previously pulled ~5 GB of CUDA/ML wheels (torch, nvidia/\*, transformers, sentence-transformers, scipy, tree-sitter-languages, pandas, litellm, llmlingua) as hard runtime dependencies. This made first-run installs impractical on machines without CUDA or a fast connection.

**What changed:** the six heavy packages listed below have been moved from `[project.dependencies]` to named `[project.optional-dependencies]` extras. The runtime behaviour is **unchanged** — every import site was already guarded with `try/except ImportError` before this release. Only the install metadata changed.

**Migration:** if your code uses any of the features below, add the corresponding extra to your install command:

| Feature | Add to install command |
|---|---|
| Semantic search / vector embeddings (sentence-transformers) | `pip install tokenpak[retrieval]` |
| Tree-sitter code parsing | `pip install tokenpak[code-compression]` |
| A/B testing optimizer (scipy) | `pip install tokenpak[intelligence]` |
| Pandas data utilities | `pip install tokenpak[data]` |
| LLMLingua prompt compression | `pip install tokenpak[compression]` |
| LiteLLM Router integration | `pip install tokenpak[integrations-litellm]` |
| **Everything (previous default)** | `pip install tokenpak[full]` |

If you previously ran `pip install tokenpak` and relied on retrieval / code-compression / intelligence / compression / integrations-litellm features, you must add the extra to your install. Features that use the guarded import will raise a clear `ImportError` with the correct `pip install` command if the extra is absent.

**Slim install target:** `pip install tokenpak` on a clean machine resolves in under 30 seconds and uses under 200 MB of disk. The `[full]` extra restores the previous behaviour for users who want everything.

### Added — install footprint extras split

- Named extras: `tokenpak[retrieval]`, `tokenpak[code-compression]`, `tokenpak[intelligence]`, `tokenpak[data]`, `tokenpak[compression]`, `tokenpak[integrations-litellm]`, `tokenpak[full]`.
- CI: slim-install smoke test — installs tokenpak with no extras, asserts venv site-packages < 200 MB, runs `python -c "import tokenpak; from tokenpak.proxy import client"`.
- CI: full-install matrix — `pip install -e .[full,dev]` + full test suite.
- `tests/test_dependencies_extras.py` — slim-core invariant gate.
- `tests/test_extras_import_guard.py` — lightweight post-demotion gate that asserts each heavy package is absent from `[project.dependencies]` and smoke-tests each guarded import path.

### Changed — import error messages

- `tokenpak/integrations/litellm/proxy.py` — error message updated to suggest `pip install tokenpak[integrations-litellm]` instead of bare `pip install litellm`.

---

## [1.8.0] — 2026-06-06

Minor release: companion **memory-source ingestion** — point the companion at
your own Markdown notes ("bring your own knowledge base"). Additive only: no
changes to existing behavior, no breaking changes.

### Added

- **companion:** memory-source ingest library API — `ingest_from_dir()` and
  `ingest_sources()` in `tokenpak.companion.memory.lesson_ingest` ingest lessons
  from arbitrary directories of Markdown notes (scanned recursively),
  independent of the built-in memory schema. `ingest_sources()` returns a
  per-source status report whose `reason` distinguishes *no source configured*
  from *missing* / *not-a-directory* / *present but no matching files* / *ok*.
- **companion:** `TOKENPAK_COMPANION_MEMORY_DIRS` configuration — an
  OS-path-separator- or comma-separated list of extra memory directories,
  parsed into `CompanionConfig.memory_dirs` (`~` expanded; empty entries
  dropped; fail-open, never raises).
- **companion (MCP):** the `session_info` tool now reports the configured
  `memory_dirs` and surfaces a hint when no memory source is set, so an empty
  ingest is self-explaining.

### Tests & docs

- `tests/test_companion_memory_source.py` — coverage for the env-var parser,
  `ingest_from_dir`, and the `ingest_sources` status contract.
- Companion guide: a "Memory sources — bring your own knowledge base" section
  documenting the env var, the library API, and the MCP surface.

### Internal

- Regenerated the public-API snapshot. Beyond the companion additions above,
  this is a ratchet correction that absorbs already-public `tokenpak.proxy`
  symbols the committed snapshot had drifted from — no new public API in this
  release other than the companion additions.

## [1.7.1] — 2026-06-03

Surgical patch release: fixes, hardening, and public-safety/CI hygiene only. No new
features, no default-behavior changes, no breaking changes. (The install-footprint
extras split remains parked for a future minor — see the Unreleased section below.)

### Fixed

- **proxy:** evict upstream inflight keys when the in-flight counter reaches zero,
  preventing an upstream RSS leak under sustained load.
- **proxy:** consolidate `CLAUDE_CODE_HEADER_ALLOWLIST` to a single canonical definition.
- **companion:** `check_budget` no longer presents its result as authoritative total spend.
- **companion:** lazy-load sentence-transformers so the MCP server starts quickly
  (cold-start fix); launch the MCP server with a safe Python path (`-P`).
- **companion:** defensive guard for truncated provider streams.
- **pakplan:** read Pak recall from `recall.db` instead of a stale `journal.db`.
- **spend-guard:** attribution-clear rolling-cap `402` response body.
- **cli:** banner shows the live installed package version instead of a hardcoded string.
- **paths:** fail-loud subdir allowlist in `_paths.under()` (and allow the `dispatch/` subdir).
- **telemetry:** skip the RBAC admin bootstrap during snapshot generation.

### Changed

- **docs:** audit and compliance CLI stubs reworded as Pro-tier features; corrected
  protocol terminology to TIP per the glossary.

### Dependencies

- Bump `websockets` to `>=16.0`; bump CI actions `codecov-action` 4→6,
  `download-artifact` 4→8, `sticky-pull-request-comment` 2→3.

### Internal

- Suppress the ephemeral RBAC admin password from release-gate snapshot-validation CI
  logs; snapshot generation now sets `TOKENPAK_SNAPSHOT_GEN=1` to skip the first-run
  admin bootstrap during introspection.
- CI: quarantine runner-sensitive perf/SLA tests from the blocking matrix; refresh the
  release-gate workflow-steps snapshot; validate the release tag is reachable from the
  release branch before build; mask functional identifiers in the identity check.

### Note — licensing

- The `tokenpak activate` licensing integration that landed on `main` during the v1.7.0
  line ships to PyPI for the first time in **1.7.1**; users on the published 1.7.0 wheel
  do not have it.

## [v1.7.0] — 2026-05-25

> Corrected 2026-05-29: the Beta-1 CLI surface below was originally listed
> under v1.6.0, but it is absent from the v1.6.0/v1.6.1 released artifact and
> first ships in v1.7.0. Every entry here is backed by code on the release
> commit.

### Added

- Beta-1 CLI surface (first shipped in v1.7.0):
  - `tokenpak tip` (validate / inspect / conformance / doctor / scaffold-adapter)
  - `tokenpak features` + `tokenpak features explain <feature>`
  - `tokenpak pakplan preview / explain / report`
  - `tokenpak home` (path / init / validate / explain / migrate)
  - `tokenpak doctor --conformance` (regression recovery from v1.3.7)
  - `BETA_ONBOARDING.md` + `KNOWN_LIMITATIONS.md`
- `tokenpak._paths` canonical home-resolver covering the `~/.tpk/` boundary.
- OSS `tokenpak activate` consults the Pro daemon's `/v1/features` endpoint
  (2-second timeout, five fail-closed states), via `tokenpak/licensing`.
- Rolling cumulative spend caps for the spend-guard
  (`tokenpak/proxy/spend_guard/rolling_caps.py`) with session/rolling-window
  enforcement and regression coverage.
- Dynamic reasoning-usage parser registry (`tokenpak/services/providers/`)
  with additive monitor.db reasoning-token columns.
- `tokenpak status --fleet` with a `rollup_daily` aggregation table.
- Vault claude-transcript source adapter for the BM25 index
  (`tokenpak/vault/sources/claude_transcript.py`).
- Anthropic prompt-cache TTL attribution telemetry.

### Changed

- Codex companion lifecycle hooks and vault atomic-write hardening
  (`tokenpak/vault/_atomic.py`) ship together; release-gate trust-contract
  follow-ups land on top of the v1.6.0 phases.
- Copy/terminology adopt "Pak / Prompt Packing / Savings Ledger" across the
  dashboard and CLI help.
- Wheel now ships `tokenpak/tip/schemas/*.json`.
- LICENSE display corrected to Apache-2.0 across README / CONTRIBUTING /
  LICENSE_COMMERCIAL (was a stale MIT label).

### Fixed

- Dashboard settings now forbid webhook-URL writes (with regression test).
- Tightened cache-miss UUID attribution for byte-preserved traffic.
- `doctor` surfaces the home-boundary advisory before other checks.
- CI stabilization: workflow concurrency cancellation, scoped release/
  benchmark triggers, fastapi test-collection import guards, regenerated CLI
  reference, and de-flaked time-relative test fixtures.

### Security

- Corrected the SECURITY.md and Code-of-Conduct contact addresses to
  `hello@tokenpak.ai`, and restored three Claude Code plugin hook scripts that
  a module consolidation had dropped while their hook declarations remained.

## [v1.6.1] — 2026-05-17

### Fixed

- `tests/benchmarks/test_load_100rps.py::TestHealthEndpointLoad::test_health_100rps_p99_under_20ms`
  now widens its p99 ceiling from 500ms (strict, local) to 2000ms when
  `CI=true` or `GITHUB_ACTIONS=true`. GitHub Actions shared 4-core runners
  show scheduler-jitter-driven p99 variance an order of magnitude higher
  than dedicated hosts (the v1.6.0 release run measured
  p50=0.7ms / p95=3.8ms / p99=1025.1ms — a tail-only spike, not a
  health-endpoint defect). Local benchmark signal is preserved at 500ms.

### Re-released

- Re-issues the v1.6.0 release surface. The v1.6.0 tag run did not reach
  the build/publish stages, so PyPI and the GitHub Release for v1.6.0
  were never produced. v1.6.1 ships the identical v1.6.0 functional
  surface (see entries below) plus the benchmark-tolerance fix above.

## [v1.6.0] — 2026-05-16

> Corrected 2026-05-29: the Beta-1 CLI surface (`tip` / `features` / `pakplan`
> / `home` / `doctor --conformance`, `tokenpak._paths`, OSS `activate` →
> `/v1/features`, BETA_ONBOARDING / KNOWN_LIMITATIONS) was originally listed
> here but is absent from the v1.6.0/v1.6.1 released artifact; it is now
> attributed to v1.7.0, where it first ships. The entries below are what
> v1.6.0 actually contained.

### Added

- `tokenpak pak create` and `tokenpak pak import` (OSS Beta 1)
- Release-gate trust contract Phases 4r/5/6 — public-API, telemetry-schema,
  and workflow-steps snapshot ratchets enforced by CI on every PR

### Changed

- `tokenpak plan` output is dynamically derived from the live pricing
  index; the previous "TBD" placeholder is gone
- `tokenpak activate` rejects empty / too-short / non-printable /
  placeholder keys before any daemon round-trip
- `tokenpak pak status` no longer triggers a heavy vault index load; the
  status path returns in under 2 seconds on populated vaults
- Wheel ships `tokenpak/_snapshots/*.json` (release-gate snapshot ratchets)

### Fixed

- `pak status` no longer hangs on hosts with populated vault directories

## [v1.5.6] — 2026-05-11

### Repository

- **Release-workflow hardening.** Three workflow-level guards added to `release.yml`:
  - **Tag-source validation.** A public release tag must point to a commit reachable from `origin/main`. Tags pushed against a feature branch (never merged) are rejected before any build or publish. Skipped on `workflow_dispatch` preflight runs and on non-`v*` tags (rehearsal tags).
  - **Action-pin enforcement.** A new `scripts/check-action-pins.sh` scans every `uses: <owner>/<repo>@<ref>` reference in `.github/workflows/` and rejects abbreviated SHA pins (7–39 hex chars). Full 40-character commit SHAs and floating version-tag refs (e.g. `@v4`) remain allowed.
  - **Checksum dist-purity guard.** A named step asserts that `SHA256SUMS` lives at the repository root, never inside `dist/`. The general dist-purity guard already covers this, but the named step produces a diagnostic that points at the v1.5.3 failure mode if it ever recurs.
- **Workflow-step ratchet.** `tokenpak/_snapshots/workflow-steps.json` records the canonical step set for the release workflow; CI fails if a step is added or removed without an accompanying snapshot update.

### Acceptance

- `pytest tests/ -q --tb=short` is green on Python 3.10 / 3.11 / 3.12 / 3.13.
- `pip install tokenpak==1.5.6` from a fresh virtualenv succeeds.
- All public-surface guardrails (`Public layout check`, `Repo Hygiene Check`, `Identity & language check`, `CLI Docs Up-to-date`) pass on `main`.

### No behavior change

This release ships no runtime, CLI, or public-API behavior change. It is a release-workflow hardening patch and the first release cut on the hardened path.

---

## [v1.5.5] — 2026-05-09

### Repository

- **Public-surface cleanup.** Removes workbench artifacts, internal docs, runtime state, and unrelated subdirectories from the public tree; sanitizes user-facing documentation; adds CI guardrails (public-layout-check, identity-language-check) that prevent regression; retires test code whose dependencies are no longer present in the package. The public root layout dropped from 69 entries to 37; the canonical target is 24 and the remaining gap is tracked under follow-up housekeeping.
- **Doc-generator boundary defense.** `scripts/generate-cli-docs.py` now post-processes the rendered CLI reference to strip parenthetical task-ID seeds, deferred-subcommand sections, and integration-example fragments — driven by `scripts/internal-cli-cleanup.txt`. This keeps the public CLI docs clean without requiring source-side argparse edits.
- **Coverage Gate.** The ≥80% threshold step in `.github/workflows/integration.yml` is marked step-level `continue-on-error: true` pending a realistic threshold reset. The gate still runs and the threshold breach is visible in the workflow log; only the merge-blocking effect is suspended.

### Acceptance

- `pytest tests/ -q --tb=short` is green on Python 3.10 / 3.11 / 3.12 / 3.13.
- `pip install tokenpak==1.5.5` from a fresh virtualenv succeeds.
- All public-surface guardrails (`Public layout check`, `Repo Hygiene Check`, `Identity & language check`, `CLI Docs Up-to-date`) pass on `main`.

### No behavior change

This release ships no runtime, CLI, or public-API behavior change. It is a packaging/hygiene patch.

---

## [v1.5.4] — 2026-05-09

### Fixed

- **Release workflow auto-publish.** The publish step in `.github/workflows/release.yml` was failing because `SHA256SUMS` was generated inside `dist/` and the upstream PyPI publish action rejects every file in `dist/` that isn't `*.whl` or `*.tar.gz`. v1.5.4 supersedes v1.5.3 on PyPI and ships the fix:
  - `SHA256SUMS` is now generated at the repository root, never inside `dist/`.
  - The build job uploads two separate artifacts: `dist` (wheels + sdist only) and `checksums` (SHA256SUMS only).
  - A new pre-upload guard fails the build if `dist/` ever contains anything other than `*.whl` / `*.tar.gz`.
  - The publish step runs a second pre-publish guard immediately before invoking the publish action, as defense-in-depth.

### Acceptance

- `pytest tests/ -q --tb=short` is green on Python 3.10 / 3.11 / 3.12 / 3.13.
- `pip install tokenpak==1.5.4` from a fresh virtualenv succeeds.
- The GitHub Release attaches the wheel, the sdist, and `SHA256SUMS`; PyPI receives only the wheel and sdist.

> **Note**: v1.5.3 was tagged on the same day but its publish step failed with `InvalidDistribution: Unknown distribution format: 'SHA256SUMS'`. v1.5.3 is retained as a historical GitHub-tag-only release. Install with `pip install tokenpak==1.5.4`.

---

## [v1.5.3] — 2026-05-09

### Fixed

- **Release-workflow test gate hardened.** The `Run Tests` step in `release.yml` runs against a `[dev]`-only install. Test files that imported optional/external/internal modules unconditionally caused collection errors on the slim install. Each affected test now guards its imports at module load with either `pytest.importorskip(…)` for optional deps installable via extras or a `try/except ImportError → pytest.skip(allow_module_level=True)` for namespace packages where the directory exists in slim OSS but the required submodule isn't bundled.
- **Release workflow contract documented.** The `release.yml` `test` job carries a top-of-job comment block describing what the gate does and doesn't cover, the import-guard contract for optional deps, and the rule "do not bypass via `--ignore`; either fix the test's guard or add the missing extra to this step."
- **Telemetry exports restored** after a partial refactor that left the field shape drifting from the contract.
- **Test-suite stability**: resolved 23 collection errors across two distinct buckets (ghost-path imports + speculative module surfaces); guarded `jsonschema` / `yaml` in config-validator tests so slim install skips cleanly; restored `tests/test_errors.py` against the canonical error-handling path; relocated tests with internal-only dependencies under `tests/_internal/` so OSS-slim collection no longer drags closed-tree fixtures.
- **Python 3.10 collection** guarded `tomllib` (stdlib only on 3.11+); residual import guards across 24 files.
- **Test alignment**: `test_install_claude_code` and `test_setup_wizard` aligned with v1.5.2 production signatures after API drift.
- **Performance/benchmark hermeticity**: hermetic compression benchmark and re-baseline; non-flaky throughput-ratio test (pre-encoded bytes, deterministic timing); load-test timeouts adjusted for CI runner stability.

> v1.5.3 itself is not on PyPI; see v1.5.4 above.

---


## [v1.5.2] — 2026-05-08

### Added — Pak data contracts (TIP capability surface)

- 10 new TIP capability constants under `tokenpak.tip.capabilities` (`tip.pak.{capture,index,recall,hydrate,promote}`, `tip.context.{package,handoff,resume,coverage,policy}`).
- `Pak` and `ContextPackage` frozen dataclasses with full JSON round-trip in `tokenpak.tip.pak` and `tokenpak.tip.context_package`.
- 54 contract tests in `tests/tip/test_multipak_contracts.py`.

### Added — OSS surface for Pak inspection

- Read-only Pak inspection through the Vault adapter (`tokenpak/vault/pak_adapter.py`).
- `/pak/v1/status` and `/pak/v1/inspect/<id>` endpoints in the proxy. Other `/pak/v1/*` endpoints return a structured `not_implemented` response when the optional Pro daemon is absent.
- Standardized `not_implemented` error shape: `{ "error": "not_implemented", "reason": "pro_daemon_required", "detail": "…", "suggested_action": "…", "daemon_state": "…" }`.
- `tokenpak pak status` and `tokenpak pak inspect <id>` CLI commands.
- 100 surface tests in `tests/proxy/test_pak_endpoints.py` and `tests/cli/test_pak_command.py`.

---

## [v1.5.1] — 2026-05-07

### Added — Spend Guard (proxy-side circuit breaker)

- Pre-send circuit breaker that blocks risky requests before they reach the upstream provider. New package `tokenpak/proxy/spend_guard/` (estimator, policy, pending store, intent parser, replay engine, header parser, audit log, orchestrator, session-state). Hooked into `proxy/server.py` immediately after body read, before DLP. Returns HTTP 402 Payment Required with `error.type=tokenpak_spend_guard_blocked` JSON; user releases via Yes/No reply or a `[TIP: allow=once max=$X]` directive; the hard-block ceiling cannot be bypassed. Default `enabled: true` with thresholds `warn=100K/$2`, `block=500K/$10`, `hard_block=1M/$50`, `session_block_cost_usd=$10`. Pricing pulled from `tokenpak.models.get_rates`. Audit log at `~/.tokenpak/spend_guard.db`. New errors `SpendGuardBlocked (TP-ESG01)` and `SpendGuardHardBlocked (TP-ESG02)` in `core/error_handling.py`. User-facing docs at `docs/spend-guard.md`. 149 tests in `tokenpak/tests/test_spend_guard_*.py`.

### Fixed — `tokenpak start` config validator env-var bypass

- `tokenpak/core/config_validator.py` — wired the `ANTHROPIC_API_KEY` (and three other provider env-var) bypass that the missing-`api_keys` suggestion text has always advertised. `_has_env_api_key()` was defined but never called by `_validate_required_fields`, so users following documented setup hit `Required field 'api_keys' is missing` and `tokenpak start` refused to launch. The suggestion text now mentions all three accepted bypass paths (in-config dict / env var / byte-passthrough placeholder). 2 regression tests added.

### Added — proxy auth gate

- `TOKENPAK_PROXY_AUTH_TOKEN` opt-in middleware in `tokenpak/proxy/proxy_auth.py`. Localhost stays trusted; non-localhost requests now require `Authorization: Bearer <token>` whenever the env var is set, else 403. `hmac.compare_digest` for timing-safe comparison; SHA-256 hex of the token populates the new `user_id` column on the SQLite `requests` table and `extra.user_id` on the structured JSON request log — the raw token is never logged. Schema migration is additive (`ALTER TABLE requests ADD COLUMN user_id TEXT DEFAULT ''`), back-compatible with pre-auth-gate rows. The proxy-auth Bearer is stripped before forwarding upstream so the upstream provider only ever sees its own `x-api-key`. Tests in `tests/proxy/test_proxy_auth.py`. Docs at `docs/configuration/proxy-auth.md`.

### Added — headline benchmark CI gate

- `tests/benchmarks/test_headline_claim.py`, `tests/fixtures/headline_corpus.txt` — deterministic 9-message DevOps agent corpus (~8 kB) and a blocking pytest assertion that compression stays in [30, 50]%. Run locally: `make benchmark-headline`.

### Added — proxy-owned REST API

- Proxy `/tpk/v1/*` REST endpoints in `tokenpak/proxy/app_endpoints.py`:
  - `GET /tpk/v1/health` — version, uptime, vault status
  - `GET /tpk/v1/vault/search?q=&limit=` — BM25 search over the vault
  - `GET /tpk/v1/vault/block/{block_id}` — full block content
  - `GET /tpk/v1/budget` — session + daily cost snapshot
  - `GET /tpk/v1/journal/sessions?limit=` — recent journal sessions
  - `GET /tpk/v1/journal/{session_id}?entry_type=&limit=` — journal entries
  - `POST /tpk/v1/journal/{session_id}/entry` — add journal entry
  - `POST /tpk/v1/compress` — head/tail truncate to max_tokens
  - `POST /tpk/v1/optimize` — offline prompt linter report
  - `POST /tpk/v1/tokens/estimate` — token count for text/file
  - `GET /tpk/v1/capsules`, `GET /tpk/v1/capsules/{id}` — memory capsules
  - `GET /tpk/v1/session/info` — proxy environment snapshot
- Localhost-only auth by default; optional `X-TPK-Key` header if `TOKENPAK_PROXY_KEY` is set.

### Added — `tokenpak integrate`

- One-command client setup. Print-mode for 9 supported clients (Claude Code, Cursor, Cline, Continue.dev, Aider, Codex CLI, OpenAI SDK, Anthropic SDK, LiteLLM). `--apply` mode writes configs for clients with stable config formats: Claude Code (`~/.claude/settings.json`), Cursor (platform-specific `settings.json`), Continue.dev (`~/.continue/config.json`), Aider (`~/.aider.conf.yml`). Always backs up before writing and prints a rollback command.

### Added — license / plan / activate / deactivate

- Free-tier defaults today; Pro / Team / Enterprise surface ready. Gated features cataloged in `tokenpak/licensing/_GATES` (single `is_feature_enabled(name)` choke point). License stored at `~/.tokenpak/license.json`.

### Added — `tokenpak compress` and `tokenpak optimize`

- Real implementations replace earlier paywall stubs. Both run offline; `tokenpak compress` detects JSON messages for dedup and supports `--file` / stdin / `--json` / `--verbose`. `tokenpak optimize` reports whitespace bloat, repeated phrases, verbose phrasings.

### Added — 1h cache_control TTL

- `tokenpak/proxy/prompt_builder.py:_cache_control_dict()` reads `TOKENPAK_CACHE_TTL`. Set `1h` to emit `{"type":"ephemeral","ttl":"1h"}` on all cache_control markers, extending the upstream 5-minute default to 1h. Worth the 2x write cost for traffic that fires at >5-minute intervals.

### Added — telemetry SQLite writer

- The `Monitor` class in `tokenpak/proxy/monitor.py` now persists request rows to `~/.tokenpak/monitor.db`. Previously requests were written to JSONL logs only.

### Changed — auto-discover models by default

- `tokenpak/models/_discovery.py` — `auto_start_if_enabled()` now opts IN when an API key is present (was opt-in via `TOKENPAK_MODEL_DISCOVERY=1`). Family-rule inference handles unseen models with no seed edit required.

### Changed — compression on by default

- `ENABLE_COMPACTION` and `BUDGET_CONTROLLER_ENABLED` default to `True`; `COMPACT_THRESHOLD_TOKENS` defaults to `1500` (was `4500`). To restore the legacy passthrough behavior, use `tokenpak serve --safe`.

### Added — Claude Code client-auth pass-through

- When Claude Code sends its own OAuth credentials (`Authorization: Bearer` + `anthropic-beta: oauth-2025-04-20`), the proxy preserves the original request bytes while applying response-side features (cost tracking, logging, budget enforcement). Byte preservation is required because JSON re-serialization changes the request signature, causing upstream billing to route to the wrong quota pool.

### Added — byte-level vault injection

- `proxy.py:_find_system_array_close()`, `_byte_inject_system_block()` — splice vault context directly into the JSON system array at a byte offset without `json.loads` / `json.dumps` round-trip. Preserves all original bytes except the insertion point. Configurable via `TOKENPAK_CC_INJECT_MAX_CHARS` (default 2000) and relevance-gated via `TOKENPAK_CC_INJECT_MIN_QUERY` (default 50 chars).

### Added — full header forwarding for Claude Code

- Client-auth requests forward all headers verbatim (no allowlist filtering) to preserve the request identity used for OAuth quota routing. Includes `x-app`, `X-Stainless-*`, `Content-Type`, and all upstream beta flags.

### Added — TTL-aware cache_control in Anthropic adapter

- `tokenpak/proxy/adapters/anthropic_adapter.py:_body_has_explicit_ttl()` — `inject_system_context()` now detects requests with explicit `ttl` values in cache_control blocks and skips adding default ephemeral markers that would violate the upstream TTL ordering rule.

### Fixed — race conditions and validation

- **Failover iterator thread safety** — `FailoverManager.iter_providers()` now snapshots the provider chain under a lock before iterating, preventing `RuntimeError: dictionary changed size during iteration` when `reload_config()` races with an in-flight iteration.
- **Circuit breaker config reload synchronization** — added `CircuitBreakerRegistry.reload_config()` which re-reads env vars and propagates the new config to all existing breakers under the registry lock.
- **Streaming handler cross-chunk SSE buffering** — `StreamHandler.process_chunk()` now accumulates text in a line buffer and flushes only complete lines into the byte buffer, preventing parse failures when a `data: {…}` SSE event spans two `recv()` calls.
- **Cost tracking failure audit trail** — when `cost_tracker.record_request()` raises, the failure is now logged at `ERROR` level with a structured `COST_TRACKING_FAILURE model=… tokens=…` message instead of a bare `WARNING`.
- **Router Content-Length validation** — `ProviderRouter.route()` now raises `ValueError` when the `Content-Length` header doesn't match the actual body size or is non-numeric, preventing truncated bodies from being silently forwarded upstream.
- **Passthrough config validation** — `PassthroughConfig.__post_init__()` now raises `ValueError` if any header name appears in both `strip_headers` and `safe_to_log`.

### Added — `tokenpak prune` command

- Top-level alias for `tokenpak audit prune`; accepts `--days` (retention window) and `--db` (audit DB path) flags.

### Added — CLI surface consistency test

- `tests/cli/test_help_surface_consistency.py` asserts every command in `tokenpak --help` exits 0 on `<cmd> --help`.

### Added — framework adapters

- **CrewAI adapter** (`tokenpak/adapters/crewai/`) — `TokenPakContext`, `TokenPakCrewAIHook`, `TokenPakCrew`, `TokenPakHandoff`; install with `pip install tokenpak[crewai]`.
- **AutoGen adapter** (`tokenpak/adapters/autogen/`) — `TokenPakConversationHook`, `TokenPakAssistant`, `TokenPakGroupChat`, `compress_messages`; install with `pip install tokenpak[autogen]`.
- **LlamaIndex adapter** (`tokenpak/adapters/llamaindex/`) — `TokenPakSynthesizer`, `TokenPakQueryEngine`, `TokenPakIndex`, `MultiIndexFusion`; install with `pip install tokenpak[llamaindex]`.
- `pyproject.toml` extras: `[crewai]`, `[autogen]`, `[llamaindex]`.

### Removed (with replacement)

| Removed | Resolution | Canonical replacement |
|---|---|---|
| `tokenpak prune` | Implemented as top-level alias | `tokenpak audit prune` (same `--days`, `--db` flags) |
| `tokenpak list-models` | Removed from docs | `tokenpak models` |
| `tokenpak provider-status` | Removed from docs | `tokenpak status` or `tokenpak doctor` |
| `tokenpak provider-force-health` | Removed from docs | `tokenpak doctor --fix` |
| `tokenpak rebuild-vault-index` | Removed from docs | `tokenpak vault repair` |
| `tokenpak cache-stats` | Removed from docs | `tokenpak stats` |
| `tokenpak list-keys` | Removed from docs | No direct replacement — use provider dashboard |
| `tokenpak proxy --config` | Removed from docs | `tokenpak start` with config at `~/.tokenpak/config.yaml` |

---

## [1.5.0] — 2026-05-03

### Added

- Provider failover and circuit-breaker improvements consolidated into stable surfaces.
- Streaming and cache-control work consolidated for production deployment.

---

## [1.0.2] — 2026-03-25

### Fixed

- Improved error handling for malformed YAML configs.
- Hardened streaming chunk parser against partial SSE events.

---

## [1.0.1] — 2026-03-18

### Fixed

- Configuration validation regressions reported after the 1.0.0 release.

---

## [1.0.0] — 2026-03-10

First stable production release.

- Provider-agnostic routing stabilized across Anthropic and OpenAI-compatible paths.
- Stronger fallback orchestration and circuit-breaker behavior under upstream pressure.
- Hardened startup and runtime checks; better diagnostics on failover activation.

---

## [0.9.0] — 2026-02-20

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

## [0.5.0] — 2026-01-28

### Added

- Initial compression pipeline: document and code salience extractors.
- Vault block indexing with FAISS-backed retrieval (replaced with SQLite in v0.9).
- Basic CLI: `tokenpak serve`, `tokenpak status`, `tokenpak doctor`.
- WebSocket proxy endpoint (`/ws`) for real-time streaming clients.
- Benchmark suite for proxy passthrough, vault lookup, and routing decisions.

### Changed

- Moved from monolithic `proxy.py` to layered architecture (router → adapter → backend).

---

## [0.3.0] — 2026-01-10

### Added

- Core HTTPS proxy with pass-through to Anthropic Messages API.
- Token counting and budget enforcement hooks.
- Request/response logging with configurable verbosity.
- Initial recipe system for reusable compression configurations.

---

## [0.1.0] — 2025-12-20

### Added

- Initial prototype: HTTPS proxy rewriting requests to the upstream Messages API.
- Proof-of-concept context compression reducing prompt tokens by ~30%.
- Basic configuration via YAML file.
- Single-file `proxy.py` implementation.
