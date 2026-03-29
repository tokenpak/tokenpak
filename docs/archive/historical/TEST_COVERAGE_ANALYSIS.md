# TokenPak Test Coverage — Gap Analysis

**Generated:** 2026-03-24
**Author:** Cali
**Source data:** `~/tokenpak/coverage.json` (most recent full run)
**Raw report saved:** `reports/coverage-2026-03-24/coverage.json`

---

## Overall Coverage

| Metric | Value |
|---|---|
| **Overall coverage** | **61%** |
| Lines covered | 28,885 |
| Lines missing | 18,510 |
| Total files | 436 |
| Files at 0% | 76 |
| Files below 80% | 184 |
| **Gap to 85% target** | **24 percentage points** |

---

## Coverage by Category

| Category | Coverage | Files | Lines Covered | Total Lines |
|---|---|---|---|---|
| `api` | 0% | 2 | 0 | 44 |
| `dashboard` | 0% | 1 | 0 | 21 |
| `middleware` | 0% | 10 | 0 | 560 |
| `telemetry` | 32% | 51 | 1,738 | 5,498 |
| `monitoring` | 45% | 5 | 239 | 537 |
| `engines` | 54% | 4 | 47 | 87 |
| `processors` | 54% | 5 | 415 | 766 |
| `connectors` | 55% | 11 | 289 | 528 |
| `root` | 57% | 66 | 6,144 | 10,721 |
| `agent` | 67% | 203 | 15,782 | 23,671 |
| `validation` | 74% | 6 | 339 | 459 |
| `integrations` | 74% | 6 | 154 | 208 |
| `proxy` | 77% | 11 | 434 | 566 |
| `cache` | 83% | 7 | 436 | 524 |
| `enterprise` | 85% | 6 | 462 | 543 |
| `formatting` | 86% | 5 | 66 | 77 |
| `adapters` | 86% | 6 | 278 | 322 |
| `handlers` | 88% | 2 | 14 | 16 |
| `intelligence` | 88% | 9 | 976 | 1,107 |
| `compaction` | 92% | 4 | 304 | 330 |
| `semantic` | 93% | 3 | 173 | 187 |
| `routing` | 93% | 2 | 154 | 165 |
| `schemas` | 94% | 5 | 145 | 154 |
| `extraction` | 97% | 4 | 173 | 179 |
| `capsule` | 98% | 2 | 123 | 125 |

---

## Top 10 Under-Tested Areas (by missing lines)

### 1. `cli.py` — 38% coverage, **2,191 lines missing**
- **What it is:** Main CLI entry point; all top-level command dispatch
- **Why it matters:** Every user-facing operation routes through here
- **Gap type:** Integration — needs CLI invocation tests, not just unit
- **Effort:** 🔴 Hard (3-4 weeks) — complex branching, subprocess-style calls, many commands

---

### 2. `agent/cli/main.py` — 9% coverage, **466 lines missing**
- **What it is:** Agent CLI sub-entry point; session management commands
- **Why it matters:** Powers `tokenpak serve`, `stop`, `session` commands
- **Gap type:** Integration — CLI process invocation
- **Effort:** 🟡 Medium (1-2 weeks) — similar to cli.py but narrower scope

---

### 3. `agent/proxy/server_async.py` — 20% coverage, **417 lines missing**
- **What it is:** Async HTTP proxy server (the live request handler)
- **Why it matters:** Critical path — all proxy requests go through here
- **Gap type:** Integration + async — needs `pytest-asyncio` patterns, mock HTTP
- **Effort:** 🔴 Hard (2-3 weeks) — async complexity, network mock overhead

---

### 4. `agent/proxy/server.py` — 61% coverage, **337 lines missing**
- **What it is:** Sync proxy server (legacy path, still in use for some flows)
- **Why it matters:** Fallback proxy path; error paths and edge cases uncovered
- **Gap type:** Unit — missing lines are mostly error handlers
- **Effort:** 🟢 Quick (3-5 days) — add error path tests, edge case mocks

---

### 5. `telemetry/dashboard/dashboard.py` — 22% coverage, **297 lines missing**
- **What it is:** Dashboard rendering, metric aggregation, display logic
- **Why it matters:** Telemetry is already a weak category (32% overall); dashboard is the user-facing layer
- **Gap type:** Unit + UI — rendering logic, data format contracts
- **Effort:** 🟡 Medium (1 week) — unit-testable rendering functions, mock DB

---

### 6. `telemetry/segmentizer.py` — 23% coverage, **297 lines missing**
- **What it is:** Segments telemetry events into time buckets for aggregation
- **Why it matters:** Drives all time-series metrics; bugs = silent bad data
- **Gap type:** Unit — pure functions, highly testable
- **Effort:** 🟢 Quick (2-3 days) — data-in / data-out, table-driven tests

---

### 7. `agent/cli/commands/doctor.py` — 0% coverage, **291 lines missing**
- **What it is:** `tokenpak doctor` — system health diagnostics command
- **Why it matters:** Primary debugging tool for users; 0% is a reliability risk
- **Gap type:** Integration — spawns subprocesses, reads system state
- **Effort:** 🟡 Medium (1 week) — mock system calls, subprocess capture

---

### 8. `agent/cli/commands/dashboard.py` — 0% coverage, **242 lines missing**
- **What it is:** CLI-rendered dashboard command (`tokenpak dashboard`)
- **Why it matters:** User-facing monitoring; 0% = no safety net for regressions
- **Gap type:** Unit + CLI integration
- **Effort:** 🟡 Medium (1 week) — similar to dashboard.py above

---

### 9. `telemetry/server.py` — 49% coverage, **268 lines missing** *(#8 by impact)*
- **What it is:** Telemetry HTTP server — accepts event POSTs from proxy
- **Why it matters:** Any bug here silently drops metrics
- **Gap type:** Integration — HTTP server tests (Flask/FastAPI style)
- **Effort:** 🟡 Medium (1 week) — use `httpx.AsyncClient` or `TestClient`

---

### 10. `processors/code_treesitter.py` — 18% coverage, **234 lines missing**
- **What it is:** Tree-sitter-based code parsing for compression context
- **Why it matters:** Drives intelligent code compression; parsing bugs = bad context
- **Gap type:** Unit — input/output transformation, language-specific cases
- **Effort:** 🟡 Medium (1 week) — fixture-based, language sample files

---

## Effort Estimate to Reach 85%

Current: **61%** → Target: **85%** → Gap: **~18,500 lines** need coverage

| Priority | Items | Est. Effort | Lines Impact |
|---|---|---|---|
| 🟢 Quick wins | `telemetry/segmentizer.py`, `server.py` error paths | 5-8 days | ~600 lines |
| 🟡 Medium | `doctor.py`, `dashboard.py`, `telemetry/server.py`, `code_treesitter.py` | 4-5 weeks | ~1,200 lines |
| 🔴 Hard | `cli.py`, `server_async.py`, `agent/cli/main.py` | 6-10 weeks | ~3,100 lines |

**Realistic path to 85%:** 3-4 months with 1 dev focused on test coverage.
**Fastest ROI path:** Quick wins + middleware stubs (~2 weeks) → gets to ~65-68%.

---

## Zero-Coverage Clusters (High Risk)

These entire modules have **no tests at all**:

- `middleware/` — 10 files, 560 lines (request/response middleware pipeline)
- `api/` — 2 files, 44 lines (public API layer)
- `dashboard/` root — 1 file
- All `agent/cli/commands/` — 15+ command files (doctor, dashboard, metrics, replay, status, etc.)

Recommend: add at minimum a **smoke test** (import + instantiate) for each 0% module to catch import-time crashes. Low effort, high safety value.

---

## Recommended Next Task

**P3: Add smoke tests for all 0% modules** — 1 week, ~76 files, prevents import crashes and establishes baseline for future improvement.
