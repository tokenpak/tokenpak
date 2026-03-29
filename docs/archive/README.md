# TokenPak Docs Archive

**Purpose:** Historical reference materials, audit reports, and project planning documents moved out of the active docs to reduce clutter.

**Last consolidated:** 2026-03-27  
**Active docs:** 48 files

---

## Archive Directories

### `audits/`

Historical audit reports and analysis:

- `audit-tokenpak-adoptability-*.md` — 3 adoptability audits (1406, 1929, 2125)
- `api-audit-report-2026-03-24.md` — API design audit
- `COVERAGE_AUDIT_20260326.md` — Test coverage audit
- `FUZZ-TESTING-REPORT-2026-03-27.md` — Fuzzing results
- `OSS-READINESS-REPORT-2026-03-26.md` — OSS readiness assessment
- `SECURITY-AUDIT-2026-03-26.md` — Security audit findings
- `STRESS_TEST_RESULTS.md` — Load testing results

**Use case:** Review past audits, compare metrics over time, check historical findings.

---

### `benchmarks/`

Performance traces and baseline measurements:

- `PERFORMANCE-BASELINE-2026-03-27.md` — Performance baseline snapshot
- `PROXY-PERF-TRACE-2026-03-26.md` — Initial performance trace
- `PROXY-PERF-TRACE-POST-FIX-2026-03-27.md` — Performance after optimization
- `PROXY-PERF-TRACE-SUE-2026-03-27.md` — Full session trace
- `compression-benchmark-2026-03-24.md` — Compression performance data

**Use case:** Compare performance across builds, debug regressions, validate optimizations.

---

### `planning/`

Project planning, PRDs, and specs:

- `PLAN-versioning-deployment.md` — Versioning & deployment plan
- `PRD-versioning-deployment.md` — Product requirements (versioning)
- `SPEC-versioning-deployment.md` — Technical spec (versioning)
- `MEMORY-LAYER-SPEC-2026-03-27.md` — Memory layer technical spec
- `LOCK-MAP-2026-03-26.md` — Lock/concurrency mapping
- `SCALING-ANALYSIS-2026-03-27.md` — Scaling characteristics analysis
- `migration.md` — Migration planning (old)
- `audit-monitor-merge-plan.md` — Audit/monitor subsystem merge planning

**Use case:** Understand past decisions, reference specs, trace requirement history.

---

### `spikes/`

Technical spikes and experiments:

- `ASYNCIO-MIGRATION-SPIKE-2026-03-26.md` — Async/await migration work

**Use case:** Review experimental work, understand path decisions.

---

### `launch/`

Launch materials and marketing:

- `hn-post.md` — Hacker News post draft
- `positioning.md` — Market positioning statement
- `reddit-post.md` — Reddit post draft
- `twitter-thread.md` — Twitter thread draft

**Use case:** Reference launch messaging, update positioning.

---

### `historical/`

Old historical docs and deprecated references:

- `KNOWN-ISSUES.md` — Historical known issues list
- `TEST_COVERAGE_ANALYSIS.md` — Old test coverage analysis
- `TEST-COVERAGE-GAPS-2026-03-27.md` — Test gap analysis

**Use case:** Check old issues (may be resolved), historical test metrics.

---

## When to Use Archive

**Move to archive when:**
- Document is > 6 months old and unlikely to change
- Audit/report is historical reference only
- Planning doc for completed feature/sprint
- Performance benchmark for older build
- Temporary spike/experiment results

**Keep in active docs when:**
- Current best practices or standards
- Features shipped and supported
- Active security/performance guidance
- Reference material used weekly
- Anything users might need

---

## Searching Archives

To find something in the archives:

```bash
# Search audit reports
grep -r "search term" archive/audits/

# Search performance traces
grep -r "latency\|throughput" archive/benchmarks/

# Search planning docs
grep -r "versioning\|deployment" archive/planning/
```

---

## Restoring Documents

If you need to restore an archived doc back to active status:

1. Move it from `archive/` back to the active docs directory
2. Update cross-references if needed
3. Add to [INDEX.md](../INDEX.md)
4. Commit: `git commit -m "docs: restore X from archive"`

---

## Statistics

| Metric | Value |
|--------|-------|
| Total files archived | 30 |
| Audits | 8 |
| Benchmarks | 5 |
| Planning docs | 8 |
| Launch materials | 4 |
| Historical | 3 |
| Spikes | 1 |

Archive created: 2026-03-27  
Reason: Documentation consolidation sprint (78 → 48 active files)

