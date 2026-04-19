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

TokenPak is one Python package at `tokenpak/tokenpak/`. All code lives under that root. Top-level subsystems each own one concern:

| Subsystem | Owns |
|---|---|
| `core/` | Types, interfaces, shared primitives. No business logic. |
| `proxy/` | HTTP server at 127.0.0.1:8766; byte-preserved passthrough to providers. |
| `companion/` | Client-side pre-send optimizer for TUI/CLI (MCP + hooks + skills). |
| `compression/` | Deterministic token-reduction pipeline (dedup, alias, segmentize, directives). |
| `cache/` | TokenPak's local compressed-payload cache (distinct from provider cache). |
| `creds/` | Credential router. Discovers creds from 5 provider surfaces; never stores what it didn't find. |
| `monitor/` (via `metrics/`) | SQLite request ledger (`monitor.db`). Writes every request row with `cache_origin`. |
| `routing/` | Request → model → provider decision. Fallback rules. |
| `cli/` | `tokenpak` entry point. Subcommand dispatch only; delegates to subsystems. |
| `dashboard/` | Local web UI. Reads from monitor DB; no business logic. |
| `sdk/` | Public Python SDK surface. Stability contract. |
| `licensing/` | License enforcement hooks. |
| `telemetry/` | Opt-in anonymous usage metrics. Off by default. |
| `recipes_oss/` | Built-in compression recipes (YAML). |
| `agent/`, `agentic/`, `orchestration/` | **Consolidation pending.** Three overlapping subsystems; see `10-release-quality-bar.md` Gate A4. |

**Rule:** a concern lives in exactly one subsystem. If you can't name which one, the subsystem doesn't exist yet — propose one in a PR before writing the code.

## 2. Dependency Direction

Dependencies flow inward only. A subsystem at level N may import from any level < N; imports at level ≥ N are forbidden.

```
Level 0  core/
Level 1  creds/   compression/   cache/   monitor/   recipes_oss/
Level 2  proxy/   companion/    routing/   telemetry/   licensing/
Level 3  cli/     sdk/          dashboard/
Level 4  (none — top of tree)
```

**Consequences:**
- `proxy/` may import `compression/` and `creds/`; `compression/` must not import `proxy/`.
- `cli/` may import anything below it; nothing imports from `cli/`.
- Two Level-1 subsystems must not import from each other. If they need to share logic, promote to `core/`.

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

- **Providers and models** — discovered via `creds/discovery.py` scanning known credential surfaces at startup. Unknown providers produce a debug log and a `provider=unknown` monitor row, never an exception.
- **Compression recipes** — loaded from `recipes_oss/*.yaml` at import time. Drop a YAML in the directory, it registers itself.
- **Integrations** (`tokenpak integrate <client>`) — one file per client under `tokenpak/cli/integrations/`. The CLI enumerates files in that directory; there is no central registry.
- **Compression stages** — classes inheriting from `compression.Stage` are picked up via `__subclasses__` at pipeline build time.

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

- **Monitor DB** (`~/.tokenpak/monitor.db`) is the only SQLite store. Schema migrations live in `metrics/migrations/`.
- **Companion stores** (`~/.tokenpak/companion/journal.db`, `capsules/`, `budget.db`) are separate files, owned by `companion/`.
- **No pickled state.** Serialize as JSON or SQLite; never pickle anything that touches disk.
- **No hidden state files** in the repo. If a test needs a fixture, it creates it in a tmpdir.

## 8. Introducing a New Subsystem

Before adding a top-level directory under `tokenpak/`:

1. Open a PR with just the directory, an `__init__.py`, and a one-paragraph "why this exists, what it owns" in the module docstring.
2. Update this standard's §1 table in the same PR.
3. Confirm the new subsystem fits the Level 0–3 hierarchy; if it doesn't, explain the addition to the hierarchy.
4. Get reviewer sign-off before merging real code into it.

Bar for a new subsystem: it owns a concern no existing subsystem owns, and at least 3 files will live there within the first PR. Don't create a subsystem for one function.

## 9. Known Architectural Debt

Tracked for the audit rubric to check against, not to prescribe fixes here:

- `agent/`, `agentic/`, `orchestration/` — three subsystems with overlapping scope; consolidation target TBD.
- Top-level `dashboard/` vs `tokenpak/dashboard/` — one of these is wrong.
- Transient work documents at repo root (`COMPLETION_REPORT.md`, `IMPLEMENTATION_SUMMARY.md`, `MERGE_CONFLICT_RESOLUTION.md`) — should move to `docs/history/` or be deleted.
- `ARCHITECTURE.md` at repo root describes TokenPak as a "Universal Content Compiler"; the Constitution identifies it as a local proxy for context compression. Reconcile or retire.
