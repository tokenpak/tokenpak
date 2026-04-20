---
title: TokenPak Architecture Standard
type: standard
status: draft
depends_on: [00-product-constitution.md]
---

# TokenPak Architecture Standard

Descends from Constitution §7. Governs where code lives, how modules depend on each other, and the rules for introducing new subsystems.

---

## 1. Package Layout

TokenPak is one Python package at `tokenpak/tokenpak/`. All code lives under that root. Top-level subsystems each own one concern. The layout below is **canonical**; any deviation in shipping code is architectural debt (tracked in §10 and in `known-findings.md`), not an alternative pattern.

| Subsystem | Owns |
|---|---|
| `proxy/` | The live request/response layer. Inbound/outbound LLM traffic: HTTP server, middleware, streaming, request-lifecycle handlers, passthrough mode, proxy-side retry and failover execution. |
| `compression/` | The optimization engine. End-to-end pipeline: segmentation, fingerprinting, strategies by content type, budgets, fidelity tiers, canon (never-touch) blocks, query rewriting, output formatting. Built-in YAML recipes live here. |
| `vault/` | The knowledge store. Indexing, retrieval, search (keyword + semantic), ranking, chunking, file parsers / AST / symbol extraction, filesystem watcher, SQLite-backed retrieval storage. Distinct from the maintainer-side Obsidian vault at `~/vault/`; the two share a name by coincidence and never share data. |
| `cache/` | Reuse instead of repeat. Semantic cache, prefix/prompt cache, tool/function schema cache, generated-artifact reuse, smart eviction, invalidation rules, cache persistence. |
| `routing/` | The decision engine. Routing rules, intent/task/model selection policies, fallback chains, provider health + circuit breakers, A/B and shadow-mode experiments, smart-routing planners. |
| `telemetry/` | System of record for measurement. Token counting, cost tracking, session telemetry, latency/error/throughput metrics, generated reports, replay metadata, audit-friendly request metadata, SQLite persistence for all of the above. |
| `companion/` | User-side helper layer. Claude Code companion, reusable memory capsules, prompt packaging, local helper utilities, session journaling. |
| `orchestration/` | Multi-step and multi-agent coordination. Workflow engine, handoffs, agent/capabilities registry, workflow state + persistence, precondition guards, retry/recovery logic, failure memory, workflow performance profiling. |
| `sdk/` | Provider and framework adapters. Per-provider adapters (OpenAI, Anthropic, Gemini, etc.), framework adapters (LiteLLM, LangChain, custom), request/response translation, adapter registry, SDK-facing auth helpers. |
| `sources/` | External and local knowledge connectors. Local filesystem, GitHub, Notion, Drive, shared/org sources, sync pipelines. |
| `core/` | Shared foundation. Not a feature bucket — the system backbone: config + defaults + env overrides, shared runtime state, schema/contract validation, shared data structures, shared error types, startup/shutdown lifecycle, license activation/validation, version checks, cooldown/rate-recovery primitives. |
| `cli/` | Terminal commands and command handlers. Individual command modules, output formatting, interactive prompts, argument parsing, entrypoint scripts. `tokenpak <verb>` dispatches here. |
| `dashboard/` | Web UI and reporting. Main web app, pages/views, UI components, filters, CSV/JSON exports, dashboard-facing stats/report APIs. Reads from telemetry; no business logic. |
| `alerts/` | Notifications, triggers, thresholds. Alert rule definitions, budget/usage/error thresholds, delivery channels (email, webhook, local notification), action triggers. |
| `security/` | Protect data and enforce boundaries. PII/DLP scanning + redaction, org/team security policies, permissions/access control, OAuth/team auth ownership, secret-safe request handling. |
| `plugins/` | Extension without core bloat. Hook points, optional plugin modules, plugin discovery and loading, example plugins. |
| `debug/` | Developer diagnostics. Structured debug logs, request/compression/routing traces, inspection helpers, health diagnostics. |

**Rule:** a concern lives in exactly one subsystem. If you can't name which one, the subsystem doesn't exist yet — propose one in a PR (§8) before writing the code.

### 1.1 Canonical subsystem set

Exactly the seventeen subsystems above. Adding new top-level subsystems goes through §8. Removing one requires migrating its concern elsewhere and updating this table in the same PR.

### 1.2 Historical reorganizations (2026-04-19)

This §1 supersedes an earlier layout that had `creds/`, `monitor/` (via `metrics/`), `recipes_oss/`, `licensing/`, and `agent/` + `agentic/` as separate top-level subsystems. Their concerns are now mapped as:

- `creds/` → `security/auth/` and `security/secrets/` (credential discovery and secret-safe handling)
- `monitor/` + `metrics/` → `telemetry/storage/` + `telemetry/metrics/` (measurement is one subsystem now)
- `recipes_oss/` → `compression/strategies/` (recipes are compression strategies)
- `licensing/` → `core/licensing/` (license logic is foundational)
- `agent/`, `agentic/`, `orchestration/` → collapsed to a single `orchestration/`

Code still sitting in the old subsystem directories on `github/main` is architectural debt; migration is tracked in `known-findings.md` with a 1.1.0 target.

## 2. Dependency Direction

Dependencies flow inward only. A subsystem at level N may import from any level < N; imports at level ≥ N are forbidden.

```
Level 0  core/
Level 1  security/   compression/   cache/   vault/   sources/   debug/
Level 2  telemetry/   proxy/   routing/   orchestration/   companion/   alerts/   plugins/
Level 3  cli/   sdk/   dashboard/
Level 4  (none — top of tree)
```

**Consequences:**
- `proxy/` may import `compression/`, `cache/`, `security/`; `compression/` must not import `proxy/`.
- `cli/`, `sdk/`, and `dashboard/` may import anything below them; nothing imports from them.
- Two subsystems at the same level must not import from each other. If they need to share logic, promote it to `core/` or the next-lower level it naturally fits.
- `orchestration/` sits at Level 2 because it coordinates workflows across `proxy/`, `cache/`, and `compression/`; it can read them but they must not know about it.

Circular imports are a build error, not a warning. Enforced in CI by the import-linter config.

## 3. Module Structure Inside a Subsystem

```
tokenpak/<subsystem>/
  __init__.py          # public surface — only names intended for external use
  _internal.py         # leading underscore = not stable API
  <feature>.py         # one concept per file; file name matches the concept
  tests/               # unit tests for this subsystem (also in top-level tests/)
```

- Public names live in `__init__.py`. If it's not re-exported there, callers must not import it.
- One public class per file unless the classes are tightly coupled (e.g., a state machine and its states).
- `_formatting/`, `_cli_core.py`, and similar underscore-prefixed modules are internal helpers and may change without notice.

## 4. Registration and Discovery

Per Constitution §5.4, no hardcoded enumerations. Use discovery patterns:

- **Providers and models** — discovered via `security/auth/discovery.py` scanning known credential surfaces at startup. Unknown providers produce a debug log and a `provider=unknown` telemetry row, never an exception.
- **Compression strategies** — loaded from `compression/strategies/*.yaml` (and Python strategy classes inheriting from `compression.Strategy`) at import time. Drop a YAML or a subclass in the directory, it registers itself.
- **Integrations** (`tokenpak integrate <client>`) — one file per client under `tokenpak/cli/integrations/`. The CLI enumerates files in that directory; there is no central registry.
- **Compression pipeline stages** — classes inheriting from `compression.Stage` are picked up via `__subclasses__` at pipeline build time.
- **Sources** (`tokenpak/sources/*`) — one subpackage per connector. Discovered by walking the `sources/` directory at startup.
- **Plugins** — `plugins/loaders/` walks `plugins/modules/` and installed entry-point plugins at startup. No central list.

**Rule:** if you find yourself writing `SUPPORTED_X = [...]`, stop. Write discovery instead.

## 5. Side Effects

- `core/` has no side effects. Importing it does nothing observable.
- `proxy/`, `companion/`, and `cli/` are the only subsystems allowed to bind sockets, spawn processes, or write to user-visible paths outside `monitor.db`.
- Disk writes outside `~/.tokenpak/` or the current working directory require explicit user consent via config.
- Tests never bind real ports or touch the user's real `~/.tokenpak/` — use pytest fixtures for temp dirs and mock servers.

## 6. Config Loading

One config chain, loaded in order, later wins:

1. Package defaults in `tokenpak/config/defaults.yaml`.
2. `~/.tokenpak/config.yaml` (user).
3. `./tokenpak.yaml` (project).
4. Environment variables prefixed `TOKENPAK_`.
5. CLI flags.

No other config paths. No `~/.config/tokenpak/`, no `XDG_CONFIG_HOME` variants. One path per layer.

## 7. State and Persistence

- **Telemetry store** (`~/.tokenpak/telemetry.db`) — the request ledger. Schema migrations live in `telemetry/storage/migrations/`. Every request row carries `cache_origin` per Constitution §5.3.
- **Vault index** (`~/.tokenpak/vault/index.db`) — knowledge-store retrieval index. Owned by `vault/storage/`.
- **Cache store** (`~/.tokenpak/cache/`) — reusable outputs, semantic matches, artifacts. Owned by `cache/storage/`.
- **Companion stores** (`~/.tokenpak/companion/journal.db`, `capsules/`, `budget.db`) — per-session assistant state, owned by `companion/`.
- **No pickled state.** Serialize as JSON or SQLite; never pickle anything that touches disk.
- **No hidden state files** in the repo. If a test needs a fixture, it creates it in a tmpdir.

## 8. Introducing a New Subsystem

Before adding a top-level directory under `tokenpak/`:

1. Open a PR with just the directory, an `__init__.py`, and a one-paragraph "why this exists, what it owns" in the module docstring.
2. Update this standard's §1 table in the same PR.
3. Confirm the new subsystem fits the Level 0–3 hierarchy; if it doesn't, explain the addition to the hierarchy.
4. Get reviewer sign-off before merging real code into it.

Bar for a new subsystem: it owns a concern no existing subsystem owns, and at least 3 files will live there within the first PR. Don't create a subsystem for one function.

## 9. Repo Root Layout

The git repository (outer `tokenpak/`) contains the Python package (inner `tokenpak/`) plus the minimum set of conventional Python-OSS-project files and dev-facing directories. The 17 canonical subsystems from §1 live **inside** the inner `tokenpak/` package, not at the repo root.

Anything that doesn't fit one of the categories below belongs inside `tokenpak/` (as a package subsystem), `tests/`, `docs/`, `examples/`, `deployments/`, or `scripts/` — not at the repo root.

### 9.1 Allowed repo-root entries

**Python packaging (required by pip and build tools, must be at root):**

| Entry | Why |
|---|---|
| `pyproject.toml` | Build metadata, `[tool.ruff]`, `[tool.mypy]`, `[project]`. Primary source of truth. |
| `setup.py` | Fallback for older pip; may become a minimal stub once pyproject-only is safe. |
| `MANIFEST.in` | Only when needed to control sdist contents. |

**Standard OSS project files (expected by contributors and GitHub):**

| Entry | Why |
|---|---|
| `README.md` | User-facing entrypoint. |
| `LICENSE` | Apache-2.0 per Constitution §1. |
| `CHANGELOG.md` | User-visible changes; format per `19-release-log-template.md`. |
| `CONTRIBUTING.md` | Contribution guide. |
| `CODE_OF_CONDUCT.md` | Community standards (if adopted). |
| `CNAME` | GitHub Pages custom domain (`tokenpak.ai`). |

**Dev harness (required for `make` / CI commands):**

| Entry | Why |
|---|---|
| `Makefile` | `make check`, `make test`, `make bench`, etc. per `10 §5.2`. |
| `.gitignore` | Repo-level ignore rules. |
| `.env.example` | Env variable template for local development. |
| `.github/` | GitHub Actions workflows, issue + PR templates, CODEOWNERS, `FUNDING.yml`. |

**Docs + tests + examples (Python OSS convention — NOT inside the package):**

| Entry | Why |
|---|---|
| `docs/` | User documentation; mkdocs-built. |
| `mkdocs.yml` | mkdocs config; must be at repo root. |
| `tests/` | Test suite; pytest's default discovery root. Subsystem-local `tokenpak/<subsystem>/tests/` unit tests are also allowed per §3, but top-level `tests/` is the integration/fast-suite home. |
| `examples/` | Usage examples (Python scripts + nested `examples/configs/` for YAML config examples). |
| `standards/` | Public project standards tree (this file is one of them). |
| `schemas/` | JSON schemas for validation-driven tooling. |
| `scripts/` | Dev + CI helper scripts (e.g., `generate-cli-docs.py`, `check-cli-docs.sh`, `audit-docs.sh`). |
| `deployments/` | User-facing self-hosting configs (AWS ECS, GCP Cloud Run, k8s, Docker Standalone, Docker Compose Full). |
| `docker/` | Dev-oriented quick-start Docker setup (`Dockerfile` + `docker-compose.yml`). Distinct from `deployments/`; this is "run it locally", `deployments/` is "ship it to prod infra." |

**Security / quality baselines:**

| Entry | Why |
|---|---|
| `bandit-baseline.json` | Known-accepted findings from the Bandit security scanner. Non-code; checked by CI. |

**The package itself:**

| Entry | Why |
|---|---|
| `tokenpak/` | The Python package, with its 17 canonical subsystems per §1. |

### 9.2 Forbidden at repo root

- **CI artifacts** — `coverage.json`, `coverage_report.txt`, `coverage*.xml`, `benchmark.json`, `benchmark-*.json`, `bench_results/`. Per `12 §6.4` these are regenerated by CI, never committed. `.gitignore` enforces.
- **Runtime state** — `.tokenpak/`, `monitor.db`, `telemetry.db`, any `*.db*`. `.gitignore` enforces.
- **Build outputs** — `dist/`, `build/`, `*.egg-info/`, `site/` (mkdocs output), `htmlcov/`. `.gitignore` enforces.
- **Duplicate trees** — e.g., a top-level `benchmarks/` when `tests/benchmarks/` exists, or a top-level `configs/` when its contents belong under `examples/configs/`.
- **Transient work docs** — `COMPLETION_REPORT.md`, `IMPLEMENTATION_SUMMARY.md`, `MERGE_CONFLICT_RESOLUTION.md`, `AUDIT*.md`, `LAUNCH_CHECKLIST*.md`. Move to `docs/history/` (internal) or delete.
- **Dated / versioned filenames** — `CHANGELOG_v1.0.md`, `ADAPTER_MATRIX_2026-03-09.md`, anything with a date or version stamp in its name. Per `feedback_no_versioned_filenames`: git handles versioning.
- **Tool caches, IDE configs** — `.idea/`, `.vscode/` (individuals may keep locally, not committed), `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`. `.gitignore` enforces.

### 9.3 Introducing a new repo-root entry

Same bar as §8 for new package subsystems:

1. Open a PR proposing the entry with a one-paragraph rationale for why it must be at the repo root (and why it can't live inside `tokenpak/`, `tests/`, `docs/`, `examples/`, `scripts/`, or `deployments/`).
2. Update this §9 table in the same PR, naming the entry and its purpose.
3. Get reviewer sign-off.

When in doubt, a new entry belongs **inside** an existing directory, not at the root. The root is reserved for things that Python tooling, GitHub, or universal OSS convention expects to find there.

---

## 10. Known Architectural Debt

Tracked for the audit rubric to check against, not to prescribe fixes here. Each item is also mirrored in `known-findings.md` (internal) with a target release.

- **Layout drift on `github/main`.** The v1.0.3 root commit (`81b87716`) has a package tree that doesn't match the §1 canonical layout: it contains `adapters/, agent/, api/, capsule/, compaction/, connectors/, engines/, enterprise/, extraction/, formatting/, handlers/, integrations/, intelligence/, middleware/, monitoring/, processors/, registry/, schemas/, semantic/, validation/` — none of which are §1 subsystems. Consolidation target: 1.1.0.
- **64 flat `.py` files at the root of `tokenpak/`.** Violates the "one concern per subsystem" rule in §1. Each needs a home. Target: 1.1.0.
- **Pre-existing `creds/`, `recipes_oss/`, `monitor/`, `metrics/`, `licensing/` subtrees.** Superseded by §1.2 remapping but the directories still exist on working branches. Migrate in-place when each gets touched; no separate migration PR required.
- **Root-level coverage artifacts.** `coverage.json` and `coverage_report.txt` at the repo root violate `12 §6.4` ("Coverage and benchmark artifacts are regenerated by CI, not edited by hand"). Removed from tracking in the v1.0.3-trix pre-promotion prep; if they reappear, `.gitignore` is missing the patterns.
- **Duplicate benchmark trees.** Top-level `benchmarks/` and `tests/benchmarks/` both exist on some branches. Canonical location is `tests/benchmarks/`. Removed in the v1.0.3 pre-promotion prep.
