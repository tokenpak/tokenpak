# Phase 2 Migration Plan — Package Layout Flattening

**Author:** Cali  
**Date:** 2026-03-28  
**Status:** Ready for Trix execution  
**Related:** `proposal-tokenpak-restructure-clean-architecture-2026-03-28.md`

---

## Summary

Phase 2 moves `packages/core/tokenpak/` → `tokenpak/tokenpak/`, dropping the `packages/core/` wrapper and the `agent/` nesting layer. This impacts 600 source files and ~487 test files across ~10,000 import statements.

---

## 1. File Count by Subsystem

### Current `packages/core/tokenpak/` layout

| Location | Files | Phase 2 Target Path |
|----------|-------|---------------------|
| **Root `.py` files** (not in subdirs) | 87 | `tokenpak/tokenpak/` (stay at root) |
| `agent/cli/` | 39 | `tokenpak/tokenpak/cli/` (flatten `commands/`) |
| `agent/proxy/` | 29 | `tokenpak/tokenpak/proxy/` (merge with existing `proxy/`) |
| `agent/agentic/` | 22 | `tokenpak/tokenpak/agentic/` |
| `agent/compression/` | 20 | `tokenpak/tokenpak/compression/` |
| `agent/vault/` | 11 | `tokenpak/tokenpak/vault/` |
| `telemetry/` | 55 | `tokenpak/tokenpak/telemetry/` |
| `proxy/` | 19 | `tokenpak/tokenpak/proxy/` (already partially done via Phase 1) |
| `agent/telemetry/` | 8 | `tokenpak/tokenpak/telemetry/` (merge with above) |
| `agent/regression/` | 8 | `tokenpak/tokenpak/regression/` |
| `agent/ingest/` | 8 | `tokenpak/tokenpak/ingest/` |
| `agent/license/` | 7 | `tokenpak/tokenpak/infrastructure/` |
| `agent/macros/` | 6 | `tokenpak/tokenpak/macros/` |
| `agent/dashboard/` | 6 | `tokenpak/tokenpak/dashboard/` |
| `agent/adapters/` | 6 | `tokenpak/tokenpak/adapters/` |
| `connectors/` | 11 | `tokenpak/tokenpak/connectors/` |
| `pro/` | 9 | `tokenpak-pro/tokenpak_pro/` |
| `intelligence/` | 9 | `tokenpak/tokenpak/intelligence/` |
| `monitoring/` | 8 | `tokenpak/tokenpak/monitoring/` |
| `cache/` | 7 | `tokenpak/tokenpak/cache/` |
| `adapters/` | 6 | `tokenpak/tokenpak/adapters/` |
| `enterprise/` | 6 | `tokenpak/tokenpak/enterprise/` |
| `validation/` | 6 | `tokenpak/tokenpak/validation/` |
| `retrieval/` | 6 | `tokenpak/tokenpak/retrieval/` |
| `integrations/` | 6 | `tokenpak/tokenpak/integrations/` |
| `schemas/` | 5 | `tokenpak/tokenpak/schemas/` |
| `processors/` | 5 | `tokenpak/tokenpak/processors/` |
| `plugins/` | 5 | `tokenpak/tokenpak/plugins/` |
| `middleware/` | 5 | `tokenpak/tokenpak/middleware/` |
| `formatting/` | 5 | `tokenpak/tokenpak/formatting/` |
| `agent/triggers/` | 4 | `tokenpak/tokenpak/triggers/` |
| `agent/team/` | 4 | `tokenpak/tokenpak/team/` |
| `agent/semantic/` | 4 | `tokenpak/tokenpak/semantic/` |
| `agent/query/` | 4 | `tokenpak/tokenpak/query/` |
| `agent/memory/` | 4 | `tokenpak/tokenpak/memory/` |
| `agent/fingerprint/` | 4 | `tokenpak/tokenpak/fingerprint/` |
| `extraction/` | 4 | `tokenpak/tokenpak/extraction/` |
| `engines/` | 4 | `tokenpak/tokenpak/engines/` |
| `compaction/` | 4 | `tokenpak/tokenpak/compaction/` |
| `agent/debug/` | 3 | `tokenpak/tokenpak/infrastructure/` |
| `agent/auth/` | 3 | `tokenpak/tokenpak/infrastructure/` |
| `agent/teacher/` | 2 | `tokenpak/tokenpak/teacher/` |
| `agent/routing/` | 2 | `tokenpak/tokenpak/routing/` |
| `semantic/` | 3 | `tokenpak/tokenpak/semantic/` |
| `cost/` | 3 | `tokenpak/tokenpak/cost/` |
| `compression/` | 3 | `tokenpak/tokenpak/compression/` |
| `cli/` | 3 | `tokenpak/tokenpak/cli/` |
| `server/` | 2 | `tokenpak/tokenpak/server/` |
| `runtime/` | 2 | Merge into `proxy/` |
| `routing/` | 2 | `tokenpak/tokenpak/routing/` |
| `metrics/` | 2 | `tokenpak/tokenpak/metrics/` |
| `handlers/` | 2 | `tokenpak/tokenpak/handlers/` |
| `capsule/` | 2 | `tokenpak/tokenpak/capsule/` |
| `api/` | 2 | `tokenpak/tokenpak/api/` |
| `dashboard/` | 1 | `tokenpak/tokenpak/dashboard/` |
| `agentic/` | 1 | `tokenpak/tokenpak/agentic/` |
| `agent/state_schemas/` | 1 | `tokenpak/tokenpak/state_schemas/` |
| `tests/` | 92 | `tests/` (keep in-package tests at root tests/) |
| **TOTAL** | **600** | — |

---

## 2. Import Rewrite Map (Current → Target)

Full sed script ready for application:

```bash
cat > /tmp/rewrite_imports.sed << 'EOF'
# agent/ subdirectory prefix drops
s/from tokenpak\.agent\.compression\./from tokenpak.compression./g
s/import tokenpak\.agent\.compression\./import tokenpak.compression./g
s/from tokenpak\.agent\.vault\./from tokenpak.vault./g
s/from tokenpak\.agent\.cli\.commands\./from tokenpak.cli./g
s/from tokenpak\.agent\.cli\./from tokenpak.cli./g
s/from tokenpak\.agent\.proxy\./from tokenpak.proxy./g
s/from tokenpak\.agent\.agentic\./from tokenpak.agentic./g
s/from tokenpak\.agent\.telemetry\./from tokenpak.telemetry./g
s/from tokenpak\.agent\.memory\./from tokenpak.memory./g
s/from tokenpak\.agent\.regression\./from tokenpak.regression./g
s/from tokenpak\.agent\.routing\./from tokenpak.routing./g
s/from tokenpak\.agent\.semantic\./from tokenpak.semantic./g
s/from tokenpak\.agent\.team\./from tokenpak.team./g
s/from tokenpak\.agent\.ingest\./from tokenpak.ingest./g
s/from tokenpak\.agent\.dashboard\./from tokenpak.dashboard./g
s/from tokenpak\.agent\.triggers\./from tokenpak.triggers./g
s/from tokenpak\.agent\.macros\./from tokenpak.macros./g
s/from tokenpak\.agent\.fingerprint\./from tokenpak.fingerprint./g
s/from tokenpak\.agent\.query\./from tokenpak.query./g
s/from tokenpak\.agent\.adapters\./from tokenpak.adapters./g
s/from tokenpak\.agent\.teacher\./from tokenpak.teacher./g

# Infrastructure consolidation (debug + license + auth → infrastructure)
s/from tokenpak\.agent\.debug\./from tokenpak.infrastructure./g
s/from tokenpak\.agent\.license\./from tokenpak.infrastructure./g
s/from tokenpak\.agent\.auth\./from tokenpak.infrastructure./g

# agent.config — maps to root config
s/from tokenpak\.agent\.config/from tokenpak.config/g
s/import tokenpak\.agent\.config/import tokenpak.config/g

# packages/core path references in sys.path/setup
s/packages\/core\/tokenpak/tokenpak\/tokenpak/g
s/packages\.core\.tokenpak/tokenpak.tokenpak/g
EOF
```

**Apply:**
```bash
find tokenpak/ tests/ packages/ -name '*.py' -exec sed -i -f /tmp/rewrite_imports.sed {} +
```

---

## 3. Top 20 High-Risk Imports (Most Referenced)

These paths appear most frequently across the codebase. Any rename error here breaks the most tests:

| Rank | Import Path | Count | Target After Rewrite |
|------|-------------|-------|----------------------|
| 1 | `tokenpak.validation.request_validator` | 47 | unchanged (not in agent/) |
| 2 | `tokenpak.agent.cli.commands.diff` | 47 | `tokenpak.cli.diff` |
| 3 | `tokenpak.config_loader` | 36 | unchanged (root module) |
| 4 | `tokenpak.agent.cli.commands.help` | 36 | `tokenpak.cli.help` |
| 5 | `tokenpak.pro.routing.detector` | 35 | → `tokenpak_pro` (Phase 5) |
| 6 | `tokenpak.cli` | 34 | unchanged (existing root cli.py) |
| 7 | `tokenpak.capsule.builder` | 33 | unchanged |
| 8 | `tokenpak.agent.agentic.validation_framework` | 31 | `tokenpak.agentic.validation_framework` |
| 9 | `tokenpak.metrics.prometheus` | 30 | unchanged |
| 10 | `tokenpak.agent.config` | 30 | `tokenpak.config` |
| 11 | `tokenpak.agent.cli.commands.workflow` | 30 | `tokenpak.cli.workflow` |
| 12 | `tokenpak.agent.agentic.handoff` | 29 | `tokenpak.agentic.handoff` |
| 13 | `tokenpak.agent.proxy.server` | 27 | `tokenpak.proxy.server` |
| 14 | `tokenpak.agent.license.activation` | 26 | `tokenpak.infrastructure.activation` |
| 15 | `tokenpak.agent.cli.commands.savings` | 26 | `tokenpak.cli.savings` |
| 16 | `tokenpak.agent.proxy.intent_policy` | 25 | `tokenpak.proxy.intent_policy` |
| 17 | `tokenpak.agent.cli.commands.optimize` | 25 | `tokenpak.cli.optimize` |
| 18 | `tokenpak.agent.cli.commands.dashboard` | 25 | `tokenpak.cli.dashboard` |
| 19 | `tokenpak.cache` | 23 | unchanged (root module) |
| 20 | `tokenpak.agent.agentic.workflow` | 23 | `tokenpak.agentic.workflow` |

**⚠️ Special case:** `tokenpak.pro.*` (35 occurrences) moves to `tokenpak_pro.*` in Phase 5 — do NOT rewrite in Phase 2 (different package name).

---

## 4. External Consumers That Need Updating

| Consumer | Count | Update Required |
|----------|-------|----------------|
| Flat `tests/*.py` files | **487 files** | Import rewrites (automated via sed) |
| `pytest.ini` | 1 | `pythonpath = tokenpak/tokenpak` (was `packages/core`) |
| `pyproject.toml` | 1 | `packages = ["tokenpak"]` pointing to `tokenpak/` |
| `Makefile` | 1 | Update install/test commands |
| `.github/workflows/*.yml` | 3 | Update `working-directory` and `PYTHONPATH` |
| `mkdocs.yml` | 1 | Update nav paths |
| `SPEC.md` | 1 | Rewrite Codebase Paths section |

---

## 5. Recommended Execution Sequence

Execute in this order to minimize breakage at each step. Each step should pass baseline (521 tests) before moving to the next.

### Step 1: Create target directory tree (no file moves yet)
```bash
mkdir -p tokenpak/tokenpak/{proxy,cli,agentic,compression,vault,telemetry,connectors,intelligence,monitoring,cache,adapters,enterprise,validation,retrieval,integrations,schemas,processors,plugins,middleware,formatting,extraction,engines,compaction,semantic,cost,routing,metrics,handlers,capsule,api,dashboard,server,memory,fingerprint,query,regression,ingest,triggers,macros,team,teacher,state_schemas,infrastructure}
```

### Step 2: Move simple subdirs first (no agent/ prefix, no conflicts)
Move these first — they have no `agent/` prefix and land directly:
- `telemetry/` → `tokenpak/tokenpak/telemetry/`
- `proxy/` → `tokenpak/tokenpak/proxy/` ← **important: Phase 1 work already here, merge carefully**
- `connectors/` → `tokenpak/tokenpak/connectors/`
- `cache/` → `tokenpak/tokenpak/cache/`
- `compaction/`, `validation/`, `retrieval/`, `integrations/`, `schemas/`
- `pro/` → hold for Phase 5

### Step 3: Move agent/ subdirs (prefix-drop)
- `agent/vault/` → `tokenpak/tokenpak/vault/`
- `agent/compression/` → `tokenpak/tokenpak/compression/`
- `agent/agentic/` → `tokenpak/tokenpak/agentic/`
- `agent/telemetry/` → merge into `tokenpak/tokenpak/telemetry/`
- `agent/dashboard/` → `tokenpak/tokenpak/dashboard/`
- `agent/regression/` + `agent/ingest/` + others

### Step 4: Move agent/cli/ (flatten commands/)
- `agent/cli/commands/*.py` → `tokenpak/tokenpak/cli/*.py` (each file, drop `commands/` layer)
- `agent/proxy/` → `tokenpak/tokenpak/proxy/` (merge with Phase 1 proxy/)

### Step 5: Consolidate infrastructure/
- `agent/debug/` + `agent/license/` + `agent/auth/` → `tokenpak/tokenpak/infrastructure/`

### Step 6: Move root-level .py files
- All 87 root `.py` files move from `packages/core/tokenpak/*.py` → `tokenpak/tokenpak/*.py`

### Step 7: Apply import rewrites
```bash
find tokenpak/ tests/ -name '*.py' -exec sed -i -f /tmp/rewrite_imports.sed {} +
```

### Step 8: Update config files
- `pytest.ini`: `pythonpath = tokenpak/tokenpak`
- `pyproject.toml`: `packages = ["tokenpak"]`, point to `tokenpak/`
- CI workflows

### Step 9: Verify
```bash
python3 -m pytest tests/unit tests/proxy tests/protocol tests/adapters tests/dashboard tests/determinism tests/feature-wave4 tests/integrations -q --tb=short -p no:xdist --override-ini="addopts=--import-mode=importlib -m 'not slow and not integration and not live and not flaky'"
# Expected: 521 passed, 0 failed
```

---

## 6. Go/No-Go Criteria for Phase 2 Kickoff

Phase 2 should start when ALL of these are true:

| Criterion | Status |
|-----------|--------|
| Phase 1 proxy decomposition complete (all files extracted from runtime/proxy.py) | ⏳ In progress (Trix) |
| `proxy/server.py`, `proxy/fallback.py`, `proxy/streaming.py`, `proxy/stats.py` exist | ⏳ Partial |
| Baseline test suite passes (521 tests, 0 failures) | ✅ Confirmed by this report |
| No in-progress tasks modifying `packages/core/tokenpak/` structure | Must verify at start |
| `RESTRUCTURE-TEST-BASELINE.md` committed | ✅ Done (commit `91b0abb83`) |

**Current blocker:** Phase 1 must complete first. Do not start Phase 2 while Trix is actively extracting proxy modules.

---

## 7. Rollback Strategy

Phase 2 is a refactor, not a feature. Rollback path:

1. **Git revert is clean** — entire Phase 2 is file moves + import rewrites, no logic changes
2. **Rollback command:**
   ```bash
   git revert HEAD~N  # where N = number of Phase 2 commits
   # OR
   git reset --hard <baseline-commit>  # use RESTRUCTURE-TEST-BASELINE.md commit hash: 91b0abb83
   ```
3. **Checkpoint commits** — Trix should commit after each Step (1-9 above), enabling surgical rollback to any step
4. **Test at every step** — if baseline drops below 521 passed, stop and diagnose before continuing

---

## Appendix: Key Paths

| File | Purpose |
|------|---------|
| `RESTRUCTURE-TEST-BASELINE.md` | Baseline test report (521 passed, commit `91b0abb83`) |
| `docs/restructure/phase2-migration-plan.md` | This file |
| `packages/core/tokenpak/` | Current source (600 files) |
| `tokenpak/tokenpak/` | Target location (Phase 2 output) |
| Trix queue | Check for active Phase 1 tasks before starting |
