# TokenPak OSS / PRO / Team / Enterprise Boundary

> **Authority:** Kevin Yang (2026-03-27)
> **Status:** Approved — this is the definitive tier assignment
> **Phase:** 1 of 5 (design doc, no code changes yet)

---

## Tier Summary

| Tier | Price | Core Value |
|------|-------|------------|
| **Free (OSS)** | $0 | Compression engine + proxy + CLI + local telemetry + vault indexing |
| **Pro** | $99/mo | Advanced compression, agentic workflows, smart routing, dashboards |
| **Team** | $299/mo | Multi-user, budget enforcement, shared vaults, OAuth |
| **Enterprise** | Custom | A/B testing, shadow mode, regression, security/DLP, audit |

---

## 1. COMPRESSION PIPELINE (14 features)

| ID | Feature | Tier | Module Path | In Core? | Action |
|----|---------|------|-------------|----------|--------|
| C1 | Content Segmentation | **Free** | `compression/` | ✅ | Stay |
| C2 | Content Fingerprinting | **Free** | `compression/fingerprint/` | ✅ | Stay |
| C3 | Code Compression | **Pro** | `agent/compression/` | ✅ ⚠️ | Move to Pro |
| C4 | Doc Compression | **Free** | `compression/` | ✅ | Stay |
| C5 | Log Compression | **Pro** | `agent/compression/` | ✅ ⚠️ | Move to Pro |
| C6 | JSON/YAML Compression | **Pro** | `agent/compression/` | ✅ ⚠️ | Move to Pro |
| C7 | Dictionary-Based Compression | **Free** | `compression/` | ✅ | Stay |
| C8 | Alias/Abbreviation Compressor | **Free** | `compression/` | ✅ | Stay |
| C9 | Token Budget Allocation | **Free** | `compression/` | ✅ | Stay |
| C10 | Fidelity Tiers | **Free** | `agent/compression/fidelity_tiers.py` | ✅ | Stay (move to `compression/`) |
| C11 | Salience Router | **Free** | `agent/compression/salience/` | ✅ | Stay (move to `compression/`) |
| C12 | Query Rewriting | **Pro** | `agent/compression/query_rewriter.py` | ✅ ⚠️ | Move to Pro |
| C13 | Compression Timeout Budget | **Free** | `proxy.py` (inline) | ✅ | Stay |
| C14 | CANON Mode | **Pro** | `capsule/` | ✅ ⚠️ | Gate behind Pro |

---

## 2. PROXY & ROUTING (15 features)

| ID | Feature | Tier | Module Path | In Core? | Action |
|----|---------|------|-------------|----------|--------|
| R1 | Request Interception | **Free** | `proxy/` | ✅ | Stay |
| R2 | Provider Detection | **Free** | `proxy/adapters/` | ✅ | Stay |
| R3 | Tool Schema Registry | **Free** | `agent/proxy/tool_schema_registry.py` | ✅ | Stay (move to `proxy/`) |
| R4 | Smart Routing | **Pro** | `routing/rules.py` | ✅ ⚠️ | Gate behind Pro |
| R5 | Fallback Chains | **Free** | `proxy/` | ✅ | Stay |
| R6 | Circuit Breaker | **Free** | `proxy/` | ✅ | Stay |
| R7 | Connection Pooling | **Enterprise** | `proxy/` (urllib3) | ✅ ⚠️ | Gate behind Enterprise |
| R8 | Intent Policy Routing | **Pro** | `agent/proxy/intent_policy.py` | ✅ ⚠️ | Move to Pro |
| R9 | Provider Translation | **Free** | `proxy/adapters/` | ✅ | Stay |
| R10 | Stream Translation | **Free** | `proxy/adapters/` | ✅ | Stay |
| R11 | Streaming Support | **Free** | `proxy.py` (inline) | ✅ | Stay |
| R12 | Per-Request Bypass | **Free** | `proxy.py` (inline) | ✅ | Stay |
| R13 | OAuth/Auth Handling | **Team** | `agent/auth/` | ✅ ⚠️ | Move to Team |
| R14 | Capsule Integration | **Pro** | `agent/proxy/` | ✅ ⚠️ | Move to Pro |
| R15 | Failover Engine | **Pro** | `routing/` | ✅ ⚠️ | Gate behind Pro |

---

## 3. COST TRACKING & TELEMETRY (10 features)

| ID | Feature | Tier | Module Path | In Core? | Action |
|----|---------|------|-------------|----------|--------|
| T1 | Cost Tracker | **Free** | `cost/` | ✅ | Stay |
| T2 | Token Counter | **Free** | `cost/` | ✅ | Stay |
| T3 | Budget Enforcement | **Team** | `budgeter.py` / `budget_controller.py` | ✅ ⚠️ | Gate behind Team |
| T4 | Session Telemetry | **Pro** | `telemetry/` | ✅ ⚠️ | Gate behind Pro |
| T5 | Cost Report Generation | **Pro** | `telemetry/` | ✅ ⚠️ | Gate behind Pro |
| T6 | Savings Calculation | **Free** | `cost/` | ✅ | Stay |
| T7 | Demo Mode | **Free** | `proxy.py` (inline) | ✅ | Stay |
| T8 | Footer Tracking | **Free** | `proxy.py` (inline) | ✅ | Stay |
| T9 | Replay System | **Pro** | `telemetry/` | ✅ ⚠️ | Gate behind Pro |
| T10 | Storage Backend | **Free** | `cost/`, `db_migrations.py` | ✅ | Stay |

---

## 4. DASHBOARD & REPORTING (8 features)

| ID | Feature | Tier | Module Path | In Core? | Action |
|----|---------|------|-------------|----------|--------|
| D1 | Web Dashboard | **Free** | `dashboard/`, `telemetry/dashboard/` | ✅ | Stay |
| D2 | FinOps Page | **Enterprise** | `telemetry/dashboard/` | ✅ ⚠️ | Gate behind Enterprise |
| D3 | Engineering Page | **Pro** | `telemetry/dashboard/` | ✅ ⚠️ | Gate behind Pro |
| D4 | Audit Page | **Enterprise** | `telemetry/dashboard/` | ✅ ⚠️ | Gate behind Enterprise |
| D5 | CSV Export | **Pro** | `telemetry/` | ✅ ⚠️ | Gate behind Pro |
| D6 | JSON Export | **Pro** | `telemetry/` | ✅ ⚠️ | Gate behind Pro |
| D7 | Session Filtering | **Pro** | `agent/dashboard/` | ✅ ⚠️ | Gate behind Pro |
| D8 | Real-Time Stats API | **Team** | `api/`, `server/` | ✅ ⚠️ | Gate behind Team |

---

## 5. VAULT INDEXING & SEARCH (8 features)

| ID | Feature | Tier | Module Path | In Core? | Action |
|----|---------|------|-------------|----------|--------|
| V1 | Vault Indexer | **Free** | `agent/vault/` | ✅ | Stay |
| V2 | AST Parser | **Free** | `agent/vault/` | ✅ | Stay |
| V3 | Symbol Extraction | **Free** | `agent/vault/` | ✅ | Stay |
| V4 | Semantic Search | **Free** | `agent/vault/`, `semantic/` | ✅ | Stay |
| V5 | Chunk Shaping | **Free** | `agent/vault/` | ✅ | Stay |
| V6 | Scoring & Ranking | **Free** | `agent/vault/` | ✅ | Stay |
| V7 | Watcher | **Free** | `agent/vault/` | ✅ | Stay |
| V8 | SQLite-Backed Retrieval | **Free** | `agent/vault/sqlite_retrieval.py` | ✅ | Stay |

> All 8 vault features are Free. No changes needed.

---

## 6. AGENTIC WORKFLOW (14 features)

| ID | Feature | Tier | Module Path | In Core? | Action |
|----|---------|------|-------------|----------|--------|
| A1 | Workflow Engine | **Pro** | `agent/agentic/proxy_workflow.py` | ✅ ⚠️ | Move to Pro |
| A2 | Capabilities Registry | **Pro** | `agent/agentic/capabilities.py` | ✅ ⚠️ | Move to Pro |
| A3 | Error Normalizer | **Free** | `agent/agentic/error_normalizer.py` | ✅ | Stay |
| A4 | Failure Memory | **Pro** | `agent/agentic/failure_memory.py` | ✅ ⚠️ | Move to Pro |
| A5 | Retry Logic | **Free** | `proxy.py` (inline) | ✅ | Stay |
| A6 | Prefetcher | **Pro** | `agent/agentic/` | ✅ ⚠️ | Move to Pro |
| A7 | Handoff System | **Team** | `agent/agentic/` | ✅ ⚠️ | Move to Team |
| A8 | Memory Promoter | **Pro** | `agent/memory/` | ✅ ⚠️ | Move to Pro |
| A9 | Precondition Gates | **Pro** | `agent/agentic/precondition_gates.py` | ✅ ⚠️ | Move to Pro |
| A10 | State Collector | **Pro** | `agent/agentic/` | ✅ ⚠️ | Move to Pro |
| A11 | Workflow Budget | **Team** | `agent/agentic/` | ✅ ⚠️ | Move to Team |
| A12 | Workflow Performance | **Pro** | `agent/agentic/` | ✅ ⚠️ | Move to Pro |
| A13 | Learning Agent | **Pro** | `agent/agentic/` | ✅ ⚠️ | Move to Pro |
| A14 | Runbook Generator | **Enterprise** | `agent/agentic/` | ✅ ⚠️ | Move to Enterprise |

---

## 7. CLI COMMANDS (27 features)

| ID | Command | Tier | Location | Action |
|----|---------|------|----------|--------|
| L1 | `serve` | **Free** | `cli/` | Stay |
| L2 | `status` | **Free** | `cli/` | Stay |
| L3 | `cost` | **Free** | `cli/` | Stay |
| L4 | `savings` | **Free** | `cli/` | Stay |
| L5 | `compress` | **Free** | `cli/` | Stay |
| L6 | `diff` | **Free** | `cli/` | Stay |
| L7 | `demo` | **Free** | `cli/` | Stay |
| L8 | `trace` | **Pro** | `cli/` | Gate behind Pro |
| L9 | `replay` | **Pro** | `cli/` | Gate behind Pro |
| L10 | `route add/list` | **Pro** | `cli/` | Gate behind Pro |
| L11 | `route test` | **Pro** | `cli/` | Gate behind Pro |
| L12 | `index` | **Free** | `cli/` | Stay |
| L13 | `vault search` | **Free** | `cli/` | Stay |
| L14 | `template` | **Free** | `cli/` | Stay |
| L15 | `config` | **Free** | `cli/` | Stay |
| L16 | `budget` | **Team** | `cli/` | Gate behind Team |
| L17 | `dashboard` | **Free** | `cli/` | Stay |
| L18 | `doctor` | **Free** | `cli/` | Stay |
| L19 | `metrics` | **Pro** | `cli/` | Gate behind Pro |
| L20 | `policy` | **Enterprise** | `cli/` | Gate behind Enterprise |
| L21 | `optimize` | **Free** | `cli/` | Stay |
| L22 | `explain` | **Free** | `cli/` | Stay |
| L23 | `profile` | **Free** | `cli/` | Stay |
| L24 | `trigger` | **Pro** | `cli/` | Gate behind Pro |
| L25 | `workflow` | **Pro** | `cli/` | Gate behind Pro |
| L26 | `bypass` | **Free** | `cli/` | Stay |
| L27 | `doctor` | **Free** | `cli/` | Stay |

---

## 8. INFRASTRUCTURE (12 features)

| ID | Feature | Tier | Module Path | Action |
|----|---------|------|-------------|--------|
| I1 | Configuration System | **Free** | `config/`, `config_loader.py` | Stay |
| I2 | Debug Logging | **Free** | `proxy.py` | Stay |
| I3 | State Management | **Free** | `runtime/` | Stay |
| I4 | Security/PII/DLP | **Enterprise** | `agent/` | Move to Enterprise |
| I5 | Error Handling | **Free** | `handlers/` | Stay |
| I6 | Version Check | **Free** | `__init__.py` | Stay |
| I7 | License Activation | **Free** | `agent/license/` | Stay |
| I8 | License Validation | **Free** | `agent/license/` | Stay |
| I9 | License Store | **Free** | `agent/license/` | Stay |
| I10 | OAuth Manager | **Team** | `agent/auth/` | Move to Team |
| I11 | Cooldown Manager | **Free** | `proxy.py` | Stay |
| I12 | Workflow Profiles | **Free** | `proxy.py` | Stay |

---

## 9. ADVANCED (8 features)

| ID | Feature | Tier | Module Path | In Core? | Action |
|----|---------|------|-------------|----------|--------|
| X1 | A/B Testing | **Enterprise** | `intelligence/` | ✅ ⚠️ | Move to Enterprise |
| X2 | Shadow Mode | **Enterprise** | `shadow_reader.py` | ✅ ⚠️ | Move to Enterprise |
| X3 | Regression Detection | **Enterprise** | `agent/regression/` | ✅ ⚠️ | Move to Enterprise |
| X4 | Baseline Registry | **Enterprise** | `agent/regression/` | ✅ ⚠️ | Move to Enterprise |
| X5 | Artifact Reuse | **Pro** | `agent/` | ✅ ⚠️ | Move to Pro |
| X6 | Team Shared Vault | **Team** | Not built | — | Build in Team |
| X7 | Agent Registry | **Team** | Not built | — | Build in Team |
| X8 | Teacher Framework | **Enterprise** | `agent/teacher/` | ✅ ⚠️ | Move to Enterprise |

---

## Diverged Module Resolution (7 modules)

These modules exist in BOTH `packages/core/tokenpak/` AND `tokenpak-pro/tokenpak_pro/`:

| Module | Core LOC | Pro LOC | Winner | Reason | Destination |
|--------|----------|---------|--------|--------|-------------|
| `fidelity_tiers` | 533 | 195 | **Core** | More complete, actively maintained | Stay in Core (Free per C10) |
| `intent_policy` | 559 | 263 | **Core** | Richer implementation | Move to Pro (R8) |
| `precondition_gates` | 430 | 395 | **Core** | Minor diverge, core is latest | Move to Pro (A9) |
| `failure_memory` | 357 | 168 | **Core** | 2× more features | Move to Pro (A4) |
| `stability_scorer` | 329 | 223 | **Core** | More complete | Move to Enterprise (X3/X4) |
| `tool_schema_registry` | 243 | 229 | **Core** | Minor diverge | Stay in Core (Free per R3) |
| `proxy_workflow` | 167 | 199 | **Pro** | Pro version is newer | Move to Pro (A1) |

**Action:** Delete all Pro copies. Use Core version as canonical. Move to destination tier per table above.

---

## Misplacement Summary

| Category | Count | % of Core |
|----------|-------|-----------|
| Pro features in OSS core | ~47 files | 8% |
| Enterprise features in OSS core | ~25 files | 4% |
| **Total misplaced** | **~72 files** | **12%** |
| Correctly placed (Free) | ~502 files | 88% |

---

## Proxy Import Audit

The live proxy (`~/tokenpak/proxy.py`, 5,992 lines) has **50+ imports** from `tokenpak.*`. Every import must be gated in Phase 2:

### Imports that stay (Free tier):
- `tokenpak.proxy.adapters` (R1, R2, R9, R10)
- `tokenpak.capsule.builder` (Free compression)
- `tokenpak.compression.*` (C1, C2, C4, C7-C9)
- `tokenpak.config_loader` (I1)
- `tokenpak.validation_gate` (Free)
- `tokenpak.agent.vault.sqlite_retrieval` (V8)

### Imports that need `try/except` gating:
- `tokenpak.agent.agentic.*` → Pro (A1, A2, A4, A6, A8-A13)
- `tokenpak.agent.proxy.intent_policy` → Pro (R8)
- `tokenpak.agent.compression.fidelity_tiers` → Free (but relocate)
- `tokenpak.agent.compression.query_rewriter` → Pro (C12)
- `tokenpak.agent.compression.salience.*` → Free (but relocate)
- `tokenpak.agent.compression.dictionary` → Free (C7, relocate)
- `tokenpak.agent.regression.*` → Enterprise (X3, X4)
- `tokenpak.agent.memory.*` → Pro (A4, A8)
- `tokenpak.cache.semantic_cache` → Pro
- `tokenpak.cache.prefix_registry` → Pro
- `tokenpak.monitoring.request_logger` → Pro
- `tokenpak.routing.rules` → Pro (R4)
- `tokenpak.budget_controller` → Team (T3)
- `tokenpak.shadow_reader` → Enterprise (X2)
- `tokenpak.skeleton_extractor` → Pro
- `tokenpak.token_manager` → Pro
- `tokenpak.telemetry.anon_metrics` → Pro (T4)
- `tokenpak.metrics.prometheus` → Pro (L19)
- `tokenpak.plugins.registry` → Pro

---

## Phase 2 Implementation Pattern

```python
# Pattern for feature-gated imports in proxy.py
try:
    from tokenpak_pro.agentic.workflow import start_proxy_workflow
    HAS_WORKFLOW = True
except ImportError:
    HAS_WORKFLOW = False

# Usage
if HAS_WORKFLOW:
    start_proxy_workflow(...)
else:
    # Graceful degradation — skip workflow orchestration
    pass
```

### `/features` endpoint (new):
```json
{
  "tier": "free",
  "features": {
    "compression": true,
    "vault_indexing": true,
    "agentic_workflow": false,
    "smart_routing": false,
    "semantic_cache": false,
    "shadow_mode": false,
    "budget_enforcement": false
  },
  "upgrade_url": "https://tokenpak.ai/pro"
}
```

---

## Next Phases

| Phase | Description | Depends On | Est. Tasks |
|-------|-------------|------------|------------|
| **1** ✅ | This document | — | 1 |
| **2** | Feature-gate proxy imports | Kevin approval of Phase 1 | 2-3 |
| **3** | Move files to `tokenpak-pro/` + resolve diverged modules | Phase 2 | 2-3 |
| **4** | Package proxy (`tokenpak serve`) | Phase 3 | 1-2 |
| **5** | Deprecate `tokenpak/pypi/` | Independent | 1 |

---

## Approval

- [ ] **Kevin** — sign-off on tier assignments (required before Phase 2)
- [ ] **Sue** — QA review of boundary doc completeness
