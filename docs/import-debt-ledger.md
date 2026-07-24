# Import-contract debt ledger

**Status:** DECLARED DEBT — every row below is a real import edge on staging that
violates the configured architecture contract and is explicitly ignored (exact,
fully-qualified, no wildcards) in `.importlinter`. The gate is **green for new
violations immediately**; these known edges burn down over time.

- **Source commit (edge inventory):** `26fa65007b47bcf77fd9bca342f4f3ab0922bd36`
  (github-staging/main at ruling time; applies to every row unless noted)
- **Policy:** declared-debt ratchet; known edges are explicit, never silent.
- **Baseline row count:** **83**. This ledger is **monotonically shrinking**:
  rows may only be removed (with the matching `.importlinter` entry, in the same
  PR that removes the physical import). Adding a row requires an explicit
  release waiver recorded with the candidate evidence. Enforced by
  `tests/lint/test_import_contracts.py`.
- **Debt acceptance authority:** each row carries a release-waiver placeholder.
  Until signed, the edge is *tolerated for gate bootstrap*, not *accepted debt*
  (`A4_PRE_BUMP: NO-GO` remains until sign-off, coverage, green run all hold).

**Classes** — `upward-import` (lower tier imports higher tier),
`entrypoint-in-lower-tier` (entrypoint surface hosted inside a lower subsystem),
`monolith-coupling` (edge into the root-level `_cli_core` monolith),
`sibling-cross-import` (one entrypoint imports another), and
`entrypoint-pipeline-bypass` (an entrypoint skips the proxy path without a
qualified documented exception).

The pinned exact baseline lives in `tests/lint/import_debt_baseline.txt`.
Current ledger edges must remain a subset of that immutable set; the gate rejects
a same-count remove-and-replace debt swap, not only a count increase.

**Documented exception set: EMPTY.** The architecture permits narrowly justified
*entrypoint → `services/` direct calls*. Twelve current direct bypass edges
lack the required exception marker and justification, so Group 9 records them
as debt rather than reclassifying them as exceptions. Any future exception
claim must name condition A, B, or C and document its architectural reason at
the call site.

## Burn-down order

Per the ruling: **Group 7 → 2 (incl. 2b) → 1 → 4 → 6 → 5 → 3**. Group 8
(direct sibling edges) was surfaced after the ruling by the sibling-independence
contract; place it alongside Group 3 because both address entrypoint-surface
inversion work. Group 9 was surfaced by the complete entrypoint boundary review
and must burn down through proxy routing or a separately reviewed, properly
marked direct-call exception.

## Group 1 — `core/runtime/proxy.py` launcher lives in Level 0 (11 edges)

Rationale: `core.runtime.proxy` is a proxy *launcher* placed in `core/`;
relocating it (to `tokenpak/runtime/` or `proxy/bootstrap.py`) plus one
injection point in `core.registry.claude_code.adapter` clears the group.
Closing tracker: `BURN-A4-G1`.

| ID | Edge (exact, as in `.importlinter`) | Class | Release waiver | Tracker | Src |
|---|---|---|---|---|---|
| IMP-001 | `tokenpak.core.runtime.proxy -> tokenpak.proxy.circuit_breaker` | upward-import | ⬜ pending | BURN-A4-G1 | 26fa650 |
| IMP-002 | `tokenpak.core.runtime.proxy -> tokenpak.proxy.config` | upward-import | ⬜ pending | BURN-A4-G1 | 26fa650 |
| IMP-003 | `tokenpak.core.runtime.proxy -> tokenpak.proxy.passthrough` | upward-import | ⬜ pending | BURN-A4-G1 | 26fa650 |
| IMP-004 | `tokenpak.core.runtime.proxy -> tokenpak.proxy.server` | upward-import | ⬜ pending | BURN-A4-G1 | 26fa650 |
| IMP-005 | `tokenpak.core.runtime.proxy -> tokenpak.proxy.headers` | upward-import | ⬜ pending | BURN-A4-G1 | 26fa650 |
| IMP-006 | `tokenpak.core.runtime.proxy -> tokenpak.proxy.vault_bridge` | upward-import | ⬜ pending | BURN-A4-G1 | 26fa650 |
| IMP-007 | `tokenpak.core.runtime.proxy -> tokenpak.proxy.adapters.utils` | upward-import | ⬜ pending | BURN-A4-G1 | 26fa650 |
| IMP-008 | `tokenpak.core.runtime.proxy -> tokenpak.proxy.request_pipeline` | upward-import | ⬜ pending | BURN-A4-G1 | 26fa650 |
| IMP-009 | `tokenpak.core.runtime.proxy -> tokenpak.proxy.monitor` | upward-import | ⬜ pending | BURN-A4-G1 | 26fa650 |
| IMP-010 | `tokenpak.core.runtime.proxy -> tokenpak.telemetry.monitoring.server` | upward-import | ⬜ pending | BURN-A4-G1 | 26fa650 |
| IMP-011 | `tokenpak.core.registry.claude_code.adapter -> tokenpak.proxy.request` | upward-import | ⬜ pending | BURN-A4-G1 | 26fa650 |

## Group 2 — `orchestration.commands → _cli_core` monolith (1 edge)

Rationale: one physical import (`orchestration/commands.py:88`) into the
root-level `_cli_core` monolith transitively couples orchestration to
`cli`/`companion`/`sdk`/`alerts` (4 broken layer pairs). Extracting the used
helper(s) into a Level ≤3 module is a one-file fix. Closing tracker:
`BURN-A4-G2`.

| ID | Edge | Class | Release waiver | Tracker | Src |
|---|---|---|---|---|---|
| IMP-012 | `tokenpak.orchestration.commands -> tokenpak._cli_core` | monolith-coupling | ⬜ pending | BURN-A4-G2 | 26fa650 |

## Group 3 — proxy hosts entrypoint surfaces (19 edges)

Rationale: `proxy/` imports `cli`/`sdk`/`companion`/`dashboard`/`alerts` to
mount their routes and helpers. The intended dependency direction is the reverse —
entrypoints register onto proxy at startup (plugins/middleware boundary), or
the shared capsule/journal/export logic promotes into `services/`. Matches Std
the entrypoint-hosting debt group. Closing tracker: `BURN-A4-G3`.

| ID | Edge | Class | Release waiver | Tracker | Src |
|---|---|---|---|---|---|
| IMP-013 | `tokenpak.proxy.server -> tokenpak.sdk.registry` | entrypoint-in-lower-tier | ⬜ pending | BURN-A4-G3 | 26fa650 |
| IMP-014 | `tokenpak.proxy.server -> tokenpak.sdk.openclaw` | entrypoint-in-lower-tier | ⬜ pending | BURN-A4-G3 | 26fa650 |
| IMP-015 | `tokenpak.proxy.server -> tokenpak.cli.goals` | entrypoint-in-lower-tier | ⬜ pending | BURN-A4-G3 | 26fa650 |
| IMP-016 | `tokenpak.proxy.app_endpoints -> tokenpak.cli.commands.optimize_prompt` | entrypoint-in-lower-tier | ⬜ pending | BURN-A4-G3 | 26fa650 |
| IMP-017 | `tokenpak.proxy.cache_invalidation_alerts -> tokenpak.alerts.channels` | entrypoint-in-lower-tier | ⬜ pending | BURN-A4-G3 | 26fa650 |
| IMP-018 | `tokenpak.proxy.app_endpoints -> tokenpak.companion.journal.store` | entrypoint-in-lower-tier | ⬜ pending | BURN-A4-G3 | 26fa650 |
| IMP-019 | `tokenpak.proxy.app_endpoints -> tokenpak.companion.journal.pak_aware` | entrypoint-in-lower-tier | ⬜ pending | BURN-A4-G3 | 26fa650 |
| IMP-020 | `tokenpak.proxy.app_endpoints -> tokenpak.companion.capsules.builder` | entrypoint-in-lower-tier | ⬜ pending | BURN-A4-G3 | 26fa650 |
| IMP-021 | `tokenpak.proxy.app_endpoints -> tokenpak.companion.budget.tracker` | entrypoint-in-lower-tier | ⬜ pending | BURN-A4-G3 | 26fa650 |
| IMP-022 | `tokenpak.proxy.app_endpoints -> tokenpak.companion.recall` | entrypoint-in-lower-tier | ⬜ pending | BURN-A4-G3 | 26fa650 |
| IMP-023 | `tokenpak.proxy.vault_bridge -> tokenpak.companion.capsules.builder` | entrypoint-in-lower-tier | ⬜ pending | BURN-A4-G3 | 26fa650 |
| IMP-024 | `tokenpak.proxy.capsule_integration -> tokenpak.companion.capsules.builder` | entrypoint-in-lower-tier | ⬜ pending | BURN-A4-G3 | 26fa650 |
| IMP-025 | `tokenpak.proxy.capsule_builder -> tokenpak.companion.capsules.builder` | entrypoint-in-lower-tier | ⬜ pending | BURN-A4-G3 | 26fa650 |
| IMP-026 | `tokenpak.proxy.server -> tokenpak.dashboard.export_api` | entrypoint-in-lower-tier | ⬜ pending | BURN-A4-G3 | 26fa650 |
| IMP-027 | `tokenpak.proxy.server -> tokenpak.dashboard.session_filter` | entrypoint-in-lower-tier | ⬜ pending | BURN-A4-G3 | 26fa650 |
| IMP-028 | `tokenpak.proxy.server -> tokenpak.dashboard` | entrypoint-in-lower-tier | ⬜ pending | BURN-A4-G3 | 26fa650 |
| IMP-029 | `tokenpak.proxy.server_async -> tokenpak.dashboard.session_filter` | entrypoint-in-lower-tier | ⬜ pending | BURN-A4-G3 | 26fa650 |
| IMP-030 | `tokenpak.proxy.server_async -> tokenpak.dashboard.export_api` | entrypoint-in-lower-tier | ⬜ pending | BURN-A4-G3 | 26fa650 |
| IMP-031 | `tokenpak.proxy.routes -> tokenpak.dashboard` | entrypoint-in-lower-tier | ⬜ pending | BURN-A4-G3 | 26fa650 |

## Group 4 — `compression.pipeline → proxy.*` (6 edges)

Rationale: Level 1 importing Level 4 for shared request/config types. Fix:
move shared types down into `core/` or pass them in via `services/` pipeline
composition. Closing tracker: `BURN-A4-G4`.

| ID | Edge | Class | Release waiver | Tracker | Src |
|---|---|---|---|---|---|
| IMP-032 | `tokenpak.compression.pipeline -> tokenpak.proxy.token_cache` | upward-import | ⬜ pending | BURN-A4-G4 | 26fa650 |
| IMP-033 | `tokenpak.compression.pipeline -> tokenpak.proxy.adapters.utils` | upward-import | ⬜ pending | BURN-A4-G4 | 26fa650 |
| IMP-034 | `tokenpak.compression.pipeline -> tokenpak.proxy.config` | upward-import | ⬜ pending | BURN-A4-G4 | 26fa650 |
| IMP-035 | `tokenpak.compression.pipeline -> tokenpak.proxy.request` | upward-import | ⬜ pending | BURN-A4-G4 | 26fa650 |
| IMP-036 | `tokenpak.compression.pipeline -> tokenpak.proxy.shadow_reader` | upward-import | ⬜ pending | BURN-A4-G4 | 26fa650 |
| IMP-037 | `tokenpak.compression.pipeline -> tokenpak.proxy.request_pipeline` | upward-import | ⬜ pending | BURN-A4-G4 | 26fa650 |

## Group 5 — vault upward imports (10 edges)

Rationale: token counting is a primitive that belongs in `core/`
(`telemetry.tokens` re-export); `proxy.config` repeats Group 4's shared-config
problem; the companion capsule type needs promotion to `core/`/`services/`.
Closing tracker: `BURN-A4-G5`.

| ID | Edge | Class | Release waiver | Tracker | Src |
|---|---|---|---|---|---|
| IMP-038 | `tokenpak.vault.search -> tokenpak.companion.memory.session_capsules` | upward-import | ⬜ pending | BURN-A4-G5 | 26fa650 |
| IMP-039 | `tokenpak.vault.indexer -> tokenpak.proxy.config` | upward-import | ⬜ pending | BURN-A4-G5 | 26fa650 |
| IMP-040 | `tokenpak.vault.indexer -> tokenpak.proxy.vault_bridge` | upward-import | ⬜ pending | BURN-A4-G5 | 26fa650 |
| IMP-041 | `tokenpak.vault.chunk_shaping -> tokenpak.proxy.config` | upward-import | ⬜ pending | BURN-A4-G5 | 26fa650 |
| IMP-042 | `tokenpak.vault.ingest.api -> tokenpak.telemetry.query.api` | upward-import | ⬜ pending | BURN-A4-G5 | 26fa650 |
| IMP-043 | `tokenpak.vault.sqlite_backend -> tokenpak.telemetry.tokens` | upward-import | ⬜ pending | BURN-A4-G5 | 26fa650 |
| IMP-044 | `tokenpak.vault.watcher -> tokenpak.telemetry.tokens` | upward-import | ⬜ pending | BURN-A4-G5 | 26fa650 |
| IMP-045 | `tokenpak.vault.indexer -> tokenpak.telemetry.tokens` | upward-import | ⬜ pending | BURN-A4-G5 | 26fa650 |
| IMP-046 | `tokenpak.vault.search -> tokenpak.telemetry.tokens` | upward-import | ⬜ pending | BURN-A4-G5 | 26fa650 |
| IMP-047 | `tokenpak.vault.retrieval.vault_index -> tokenpak.telemetry.tokens` | upward-import | ⬜ pending | BURN-A4-G5 | 26fa650 |

## Group 6 — telemetry upward imports (6 edges)

Rationale: `proxy/stats.py` is measurement and belongs in `telemetry/` per Std
canonical measurement ownership (moving it inverts 3 edges); shadow
hook/reader interfaces belong at the `services/` boundary. Closing tracker:
`BURN-A4-G6`.

| ID | Edge | Class | Release waiver | Tracker | Src |
|---|---|---|---|---|---|
| IMP-048 | `tokenpak.telemetry.pipeline -> tokenpak.proxy.shadow_hook` | upward-import | ⬜ pending | BURN-A4-G6 | 26fa650 |
| IMP-049 | `tokenpak.telemetry.pipeline -> tokenpak.proxy.shadow_reader` | upward-import | ⬜ pending | BURN-A4-G6 | 26fa650 |
| IMP-050 | `tokenpak.telemetry.model_analytics -> tokenpak.proxy.stats` | upward-import | ⬜ pending | BURN-A4-G6 | 26fa650 |
| IMP-051 | `tokenpak.telemetry.monitoring.health -> tokenpak.proxy.stats` | upward-import | ⬜ pending | BURN-A4-G6 | 26fa650 |
| IMP-052 | `tokenpak.telemetry.demo -> tokenpak.proxy.stats` | upward-import | ⬜ pending | BURN-A4-G6 | 26fa650 |
| IMP-053 | `tokenpak.telemetry.server -> tokenpak.companion.capsules` | upward-import | ⬜ pending | BURN-A4-G6 | 26fa650 |

## Group 7 — `routing.fallback → orchestration.retry` (1 edge)

Rationale: Level 2 importing Level 4 for a generic retry helper; move the
primitive to `core/`. Smallest single fix in the set — first in the burn-down
order. Closing tracker: `BURN-A4-G7`.

| ID | Edge | Class | Release waiver | Tracker | Src |
|---|---|---|---|---|---|
| IMP-054 | `tokenpak.routing.fallback -> tokenpak.orchestration.retry` | upward-import | ⬜ pending | BURN-A4-G7 | 26fa650 |

## Group 2b — cli entry modules → `_cli_core` monolith (6 edges)

Rationale: surfaced by the sibling-independence contract (post-ruling, same
staging base). Each is a real direct edge into the root-level monolith and the
vehicle of every transitive `cli → companion/sdk/alerts/dashboard` sibling
violation. Burns down with Group 2 (`_cli_core` decomposition). Closing
tracker: `BURN-A4-G2`.

| ID | Edge | Class | Release waiver | Tracker | Src |
|---|---|---|---|---|---|
| IMP-055 | `tokenpak.cli.__main__ -> tokenpak._cli_core` | monolith-coupling | ⬜ pending | BURN-A4-G2 | 26fa650 |
| IMP-056 | `tokenpak.cli.main -> tokenpak._cli_core` | monolith-coupling | ⬜ pending | BURN-A4-G2 | 26fa650 |
| IMP-057 | `tokenpak.cli.commands.doctor -> tokenpak._cli_core` | monolith-coupling | ⬜ pending | BURN-A4-G2 | 26fa650 |
| IMP-058 | `tokenpak.cli.commands.integrate -> tokenpak._cli_core` | monolith-coupling | ⬜ pending | BURN-A4-G2 | 26fa650 |
| IMP-059 | `tokenpak.cli.commands.menu -> tokenpak._cli_core` | monolith-coupling | ⬜ pending | BURN-A4-G2 | 26fa650 |
| IMP-060 | `tokenpak.cli.commands.permissions -> tokenpak._cli_core` | monolith-coupling | ⬜ pending | BURN-A4-G2 | 26fa650 |

## Group 8 — direct entrypoint-sibling edges (11 edges)

Rationale: surfaced by the sibling-independence contract (post-ruling, same
staging base). One entrypoint importing another directly violates
entrypoint independence; shared logic belongs in `services/` or a lower
primitive. Sequence this work with Group 3. Closing tracker:
`BURN-A4-G8`.

| ID | Edge | Class | Release waiver | Tracker | Src |
|---|---|---|---|---|---|
| IMP-061 | `tokenpak.cli.commands.pak -> tokenpak.companion.journal.pak_aware` | sibling-cross-import | ⬜ pending | BURN-A4-G8 | 26fa650 |
| IMP-062 | `tokenpak.cli.commands.pakplan -> tokenpak.companion.recall` | sibling-cross-import | ⬜ pending | BURN-A4-G8 | 26fa650 |
| IMP-063 | `tokenpak.cli.commands.uninstall -> tokenpak.companion.codex.uninstall` | sibling-cross-import | ⬜ pending | BURN-A4-G8 | 26fa650 |
| IMP-064 | `tokenpak.cli.commands.doctor -> tokenpak.companion.stream` | sibling-cross-import | ⬜ pending | BURN-A4-G8 | 26fa650 |
| IMP-065 | `tokenpak.cli.commands.install -> tokenpak.sdk.openclaw` | sibling-cross-import | ⬜ pending | BURN-A4-G8 | 26fa650 |
| IMP-066 | `tokenpak.cli.commands.alerts -> tokenpak.alerts.channels.slack` | sibling-cross-import | ⬜ pending | BURN-A4-G8 | 26fa650 |
| IMP-067 | `tokenpak.cli.commands.alerts -> tokenpak.alerts.channels.webhook` | sibling-cross-import | ⬜ pending | BURN-A4-G8 | 26fa650 |
| IMP-068 | `tokenpak.companion.launcher -> tokenpak.cli.commands.status` | sibling-cross-import | ⬜ pending | BURN-A4-G8 | 26fa650 |
| IMP-069 | `tokenpak.companion.launcher -> tokenpak.cli.commands.permissions` | sibling-cross-import | ⬜ pending | BURN-A4-G8 | 26fa650 |
| IMP-070 | `tokenpak.companion.codex.launcher -> tokenpak.cli.commands.permissions` | sibling-cross-import | ⬜ pending | BURN-A4-G8 | 26fa650 |
| IMP-071 | `tokenpak.sdk.registry -> tokenpak.cli.commands.install` | sibling-cross-import | ⬜ pending | BURN-A4-G8 | 26fa650 |

## Group 9 — entrypoint bypasses of proxy routing (12 edges)

Rationale: these entrypoint modules call `services/` or pipeline primitives
directly without a qualifying documented exception. They remain declared debt
until routed through the proxy or individually justified under condition A,
B, or C. Closing tracker: `BURN-A4-G9`.

| ID | Edge | Class | Release waiver | Tracker | Src |
|---|---|---|---|---|---|
| IMP-072 | `tokenpak.cli.commands._config_optimize -> tokenpak.services.memory_optimization` | entrypoint-pipeline-bypass | ⬜ pending | BURN-A4-G9 | ae1e139 |
| IMP-073 | `tokenpak.cli.commands.compress_cmd -> tokenpak.compression.dedup` | entrypoint-pipeline-bypass | ⬜ pending | BURN-A4-G9 | ae1e139 |
| IMP-074 | `tokenpak.cli.commands.compress_cmd -> tokenpak.compression.engines.heuristic` | entrypoint-pipeline-bypass | ⬜ pending | BURN-A4-G9 | ae1e139 |
| IMP-075 | `tokenpak.cli.commands.fingerprint -> tokenpak.compression.fingerprinting.generator` | entrypoint-pipeline-bypass | ⬜ pending | BURN-A4-G9 | ae1e139 |
| IMP-076 | `tokenpak.cli.commands.fingerprint -> tokenpak.compression.fingerprinting.privacy` | entrypoint-pipeline-bypass | ⬜ pending | BURN-A4-G9 | ae1e139 |
| IMP-077 | `tokenpak.cli.commands.fingerprint -> tokenpak.compression.fingerprinting.sync` | entrypoint-pipeline-bypass | ⬜ pending | BURN-A4-G9 | ae1e139 |
| IMP-078 | `tokenpak.cli.commands.preview -> tokenpak.compression.core` | entrypoint-pipeline-bypass | ⬜ pending | BURN-A4-G9 | ae1e139 |
| IMP-079 | `tokenpak.sdk.integrations.claude_code.mcp_server -> tokenpak.compression.budgets.policy` | entrypoint-pipeline-bypass | ⬜ pending | BURN-A4-G9 | ae1e139 |
| IMP-080 | `tokenpak.sdk.integrations.claude_code.mcp_server -> tokenpak.compression.extraction.extractor` | entrypoint-pipeline-bypass | ⬜ pending | BURN-A4-G9 | ae1e139 |
| IMP-081 | `tokenpak.sdk.integrations.litellm.formatter -> tokenpak.compression.engines` | entrypoint-pipeline-bypass | ⬜ pending | BURN-A4-G9 | ae1e139 |
| IMP-082 | `tokenpak.sdk.integrations.litellm.formatter -> tokenpak.compression.engines.base` | entrypoint-pipeline-bypass | ⬜ pending | BURN-A4-G9 | ae1e139 |
| IMP-083 | `tokenpak.sdk.integrations.litellm.formatter -> tokenpak.compression.wire` | entrypoint-pipeline-bypass | ⬜ pending | BURN-A4-G9 | ae1e139 |

---

*Do not edit by hand without updating `.importlinter` in the same commit — the
ledger-integrity test fails on any mismatch.*
