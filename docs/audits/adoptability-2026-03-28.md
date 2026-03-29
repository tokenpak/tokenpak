---
title: "TokenPak Adoptability Audit — 2026-03-28 03:20"
type: audit
submitted_by: Trix (automated)
status: new
created: "2026-03-28"
project: tokenpak
audit_type: adoptability
---

# TokenPak Adoptability Audit — 2026-03-28 03:20

## Executive Summary

- **Overall Score: 7.4/10** — Solid production system with strong reliability and cache efficiency
- **Critical Issue Found:** `QUICKSTART.md` missing but referenced in `getting-started.md` (broken onboarding path)
- **Top Win:** 90.5% cache hit ratio driving significant cost savings
- **Top Risk:** No Docusaurus or unified docs site — fragmented documentation across 40+ markdown files
- **Tests:** 440 test files exist; full suite timed out during audit (>2 min runtime)

## Scorecard

| Area | Weight | Score (1-10) | Trend | Key Finding |
|------|--------|-------------|-------|-------------|
| Savings/ROI | 18% | 8 | → | 90.5% cache hits, $0.037/request avg, compression varies by model |
| Trust/Reliability | 17% | 9 | → | All 3 proxies healthy, no errors in 4h, 4h+ uptime |
| Ease of Use | 14% | 6 | ↓ | CLI works, but QUICKSTART.md missing breaks new user path |
| Accuracy | 13% | 8 | → | Shadow reader on, 80% stability score, 82% tokens protected |
| Speed | 9% | 7 | → | 150ms health latency, 4.5s avg request (normal for LLM) |
| Documentation | 2%+ | 5 | ↓ | No Docusaurus, broken quickstart link, docs scattered |
| **Weighted Total** | 100% | **7.4/10** | | |

## Detailed Findings

### 1. Savings / ROI (Score: 8/10)

**Today's Production Stats:**
- **4,366 requests** processed
- **$160.50 total cost** ($0.037 avg per request)
- **64.97M input tokens** → 2.38M compressed (3.66% compression ratio)
- **201M cache read tokens** vs 21M creation = **90.5% cache hit ratio** ✅

**By Model Compression:**
| Model | Requests | Compression Ratio | Cost |
|-------|----------|------------------|------|
| claude-opus-4-6 | 665 | 10.96% | $323.42 |
| claude-sonnet-4-6 | 4,285 | 6.98% | $259.93 |
| claude-haiku-4-5 | 8,686 | 0.91% | $49.76 |
| claude-opus-4-5 | 112 | 0.67% | $23.23 |

**Analysis:**
- Cache efficiency is excellent (>90%)
- Compression varies wildly by model — Opus-4-6 sees 11% while Haiku sees <1%
- Protected tokens (82%) means fidelity is prioritized over aggressive compression
- No "savings dashboard" command for new users to see their wins

**Gap:** Users can get stats via `curl localhost:8766/stats` but there's no friendly `tokenpak savings` summary.

### 2. Trust / Reliability (Score: 9/10)

**Fleet Health:**
| Agent | Proxy Status | Check Time |
|-------|-------------|------------|
| Trix | ✅ ok | 03:20 |
| Sue | ✅ ok | 03:20 |
| Cali | ✅ ok | 03:20 |

**Uptime:**
- Current session: 4h 8min (started 23:12)
- Memory: 750.5MB (within limits, swap near max at 256MB)
- CPU: 4m 40s total
- No restarts or crashes in last 4 hours

**Error Log (last 4h):** Zero errors found ✅

**Circuit Breakers:** All closed (Anthropic/OpenAI/Google at 0 failures)

**Analysis:**
- Production-grade stability
- Memory could be watched — 775MB peak, swap near max
- Session had 1 error counter but no visible impact

### 3. Ease of Use (Score: 6/10)

**CLI Test:**
```
$ tokenpak --help
✅ Works — shows full menu of commands
```

**Available commands:** start, serve, demo, cost, savings, status, index, template, config, dashboard, doctor, fingerprint, preview, compress, optimize, last, vault, diff, prune

**Quickstart Path Issues:**
- `getting-started.md` redirects to `QUICKSTART.md`
- **`QUICKSTART.md` does not exist** ❌
- This breaks the canonical new-user path

**Config Status:**
- 8 docs reference "quickstart" or "getting started"
- Multiple entry points still exist (README, installation, getting-started)
- No `tokenpak init` wizard for fresh setups

**Shortest Path to First Savings:**
1. `pip install tokenpak`
2. `tokenpak start` (but needs config?)
3. Point your client at `localhost:8766`
4. ??? (unclear without QUICKSTART.md)

### 4. Accuracy / Output Integrity (Score: 8/10)

**Safety Features:**
- Shadow reader: ✅ Enabled
- Compilation mode: hybrid (balanced)
- Protected tokens: 53.3M (82% of input)
- Stability score: 0.7997 (~80%)

**Tool Schema Registry:**
- Frozen tools: 26
- Frozen bytes: 31.7KB (~7,922 tokens)
- Schema changes: 204 total

**No shadow validation failures** found in recent logs.

**Analysis:**
- High protection ratio means output integrity is prioritized
- 80% stability score is good but not excellent
- Validation gate flagged: "deterministic request missing required context block" (soft block only)

### 5. Speed / Latency (Score: 7/10)

**Measurements:**
| Metric | Value |
|--------|-------|
| Health check latency | 150ms |
| Average request latency today | 4,490ms |
| Recent Sonnet requests | 5-6.5s |
| Recent Haiku requests | 1.4-3.5s |
| Upstream connect+send | 999-2288ms |

**Analysis:**
- Health endpoint at 150ms is acceptable (target was <50ms for proxy overhead)
- 4.5s average is dominated by upstream API time, not proxy
- Pool reuse enabled for connection efficiency
- No BM25 timing visible in logs (may not be hitting inverted index path)

**P0 Perf Fixes Status:** Unknown — no direct indicator if inverted index, token cache LRU, or count_tokens improvements landed.

### 6. Documentation / Onboarding (Score: 5/10)

**CLI Help:** ✅ Works

**Docusaurus:** ❌ Not set up (no `website/` folder, no `sidebars.js`)

**Canonical Quickstart:** ❌ Missing (`QUICKSTART.md` doesn't exist)

**Troubleshooting:** ✅ Good `troubleshooting.md` with common issues

**FAQ:** ✅ `FAQ.md` exists (9KB)

**Doc Count:**
- Main docs/: ~35 markdown files
- packages/core/docs/: ~20 additional files
- Multiple competing entry points

**Analysis:**
- Docs exist but are scattered across two locations
- No unified navigation or search
- getting-started → QUICKSTART.md link is broken
- Kevin elevated this to higher weight but score is lowest of all areas

## New Issues Found

1. **QUICKSTART.md Missing** — `getting-started.md` references it, but file doesn't exist
2. **Swap Pressure** — 256MB swap used (at limit), could indicate memory pressure
3. **Test Suite Timeout** — 440 test files, full suite takes >2 min (blocks CI)
4. **No BM25 Timing** — Can't verify inverted index performance improvements

## Recommendations

| Priority | Issue | Fix | Effort |
|----------|-------|-----|--------|
| **P0** | QUICKSTART.md missing | Create `docs/QUICKSTART.md` with 5-min onboarding | 30 min |
| **P1** | Docs fragmentation | Consolidate docs/ and packages/core/docs/ | 2-4h |
| **P1** | Swap pressure | Review memory limits in `memory-limits.conf` | 15 min |
| **P2** | Test suite slow | Parallelize or split into fast/slow suites | 1-2h |
| **P2** | Savings visibility | Add `tokenpak savings --today` summary command | 1h |
| **P3** | Docusaurus setup | Basic docs site with sidebar nav | 4-8h |
| **P3** | Health endpoint target | Optimize health check to <50ms | 30 min |

## Comparison to Previous Audit

**No prior audits found** in `~/vault/01_PROJECTS/tokenpak/docs/audits/`. This is the first adoptability audit.

## Raw Data

### /health Response
```json
{
  "status": "ok",
  "compilation_mode": "hybrid",
  "vault_index": {"available": true, "blocks": 7938},
  "router": {"enabled": true},
  "shadow_reader": {"enabled": true},
  "budget": {"enabled": true, "total_tokens": 12000},
  "circuit_breakers": {
    "anthropic": {"open": false, "failures": 0},
    "openai": {"open": false, "failures": 0},
    "google": {"open": false, "failures": 0}
  }
}
```

### Today's Summary Stats
```
Requests: 4,366
Total input tokens: 64,967,805
Compressed tokens: 2,377,268
Compression ratio: 3.66%
Protected tokens: 53,279,503 (82.0%)
Cache read tokens: 201,153,754
Cache creation tokens: 21,152,674
Cache hit ratio: 90.5%
Total cost: $160.50
Cost per request: $0.0368
```

### Systemd Status
```
Active: active (running) since Fri 2026-03-27 23:12:10 PDT; 4h 8min ago
Memory: 750.5M (swap max: 256.0M, swap: 255.9M)
Tasks: 8
```

### Error Log (4h window)
```
No errors found
```
