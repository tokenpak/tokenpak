---
title: "TokenPak Adoptability Audit — 2026-03-27 14:06"
type: audit
submitted_by: Trix (automated)
status: new
created: "2026-03-27"
project: tokenpak
audit_type: adoptability
---

# TokenPak Adoptability Audit — 2026-03-27 14:06

## Executive Summary

- **Caching delivers massive wins** — 96% cache hit rate (124/129), semantic cache active, driving fast repeated queries
- **Speed overhead is significant** — 567ms (84%) overhead on non-cached requests; needs optimization
- **CLI works** — `tokenpak --help` returns clean output, no crash
- **Documentation exists but scattered** — good QUICKSTART.md and FAQ.md, but no MkDocs Material, no single canonical entry point
- **Fleet unreachable** — Sue and Cali both timed out on SSH health check (5s timeout)

## Scorecard

| Area | Weight | Score (1-10) | Trend | Key Finding |
|------|--------|-------------|-------|-------------|
| Savings/ROI | 18% | 7 | → | $276/day spend, cache saves tokens but compression ratio low (3.4%) |
| Trust/Reliability | 17% | 6 | ↓ | Proxy up 27min, fleet unreachable, 0 errors but soft-blocks in logs |
| Ease of Use | 14% | 7 | ↑ | CLI works, quickstart exists, config still fragmented |
| Accuracy | 13% | 8 | → | Shadow reader enabled, validation gate active, fidelity L4 |
| Speed | 9% | 4 | ↓ | 84% overhead (567ms) on non-cached requests — RED FLAG |
| Documentation | 2%+ | 6 | ↑ | QUICKSTART.md solid, FAQ exists, no MkDocs Material |
| **Weighted Total** | 100% | **6.4/10** | **→** | Solid foundation, speed is the blocker |

## Detailed Findings

### 1. Savings / ROI (Score: 7/10)

**Today's Stats (6,015 requests):**
- Input tokens: 91,999,130
- Output tokens: 1,332,673
- Total cost: $276.75
- Protected tokens: 79,998,105 (87%)
- Compressed tokens: 2,813,965 (3.4% compression ratio)
- Cache read tokens: 269,669,841
- Cache creation tokens: 24,336,044

**Analysis:**
- Compression ratio is low (3.4%) — most tokens protected, few actually compressed
- Cache read tokens (269M) far exceed input tokens (92M) — cache is doing heavy lifting
- Injected tokens (9.1M) indicate vault context working

**By Model (today):**
| Model | Requests | Cost | Compression Ratio |
|-------|----------|------|-------------------|
| opus-4-6 | 588 | $294.07 | 11.9% |
| sonnet-4-6 | 3,423 | $201.35 | 6.2% |
| haiku-4-5 | 7,772 | $44.81 | 0.9% |
| opus-4-5 | 42 | $9.09 | 0.5% |

**Gap:** No easy way for new user to see savings. `tokenpak savings` CLI exists but needs prominent placement in docs.

### 2. Trust / Reliability (Score: 6/10)

**Proxy Status:**
- Status: `active (running)`
- Uptime: 27 minutes (recent restart)
- Memory: 946.6MB (reasonable)
- Errors: 0 (this session)
- Circuit breakers: All closed (healthy)

**Fleet Status:**
- ❌ Sue (suewu): TIMEOUT or UNREACHABLE
- ❌ Cali (calibot): TIMEOUT or UNREACHABLE

**Log Analysis (last 4 hours):**
- No errors/crashes/timeouts found
- 3 validation gate soft-blocks: "deterministic request missing required context block"

**Cache Health:**
- Hit rate: 96% (124 hits / 5 misses)
- Miss reasons: timestamp_poison (3), schema_tool_change (2)

**Concern:** Fleet unreachable suggests network/SSH issues. Low uptime (27min) indicates recent restart — need to monitor stability.

### 3. Ease of Use (Score: 7/10)

**CLI Test:**
```
✓ tokenpak --help works
✓ Clean output with categorized commands
✓ Quick Start section (start, serve, demo, cost, savings, status)
✓ Tools section (index, template, config, dashboard, doctor, etc.)
```

**Install Path:**
1. `pip install tokenpak` — exists
2. Start proxy — documented
3. Point SDK at proxy — documented with examples

**Gaps:**
- Config still split across multiple files
- No unified `tokenpak init` wizard
- Multiple quickstart docs (README, QUICKSTART.md, docker-quickstart.md)

### 4. Accuracy / Output Integrity (Score: 8/10)

**Shadow Reader:** Enabled ✓
**Validation Gate:** Enabled ✓
**Compilation Mode:** Hybrid
**Active Profile:** Balanced
**Fidelity Tier:** L4_SUMMARY
**Protected Tokens:** 1,927,126 (session), 79,998,105 (today)

**Analysis:**
- High protection rate indicates conservative approach
- Shadow reader validates output integrity
- Validation gate catching soft issues (soft-block, not hard-block)
- No evidence of output degradation in logs

**Stability Score:** 0.9912 (excellent)

### 5. Speed / Latency (Score: 4/10) ⚠️ RED FLAG

**Measured Latency (unique prompts, no cache):**
| Path | Time | 
|------|------|
| Direct API (claude-haiku) | 674ms |
| Through proxy | 1,241ms |
| **Overhead** | **567ms (84%)** |

**Cached Request:**
| Path | Time |
|------|------|
| Through proxy (cache hit) | 210ms |

**Stats Endpoint:**
- p50 latency: 1,903ms
- p99 latency: 33,678ms

**Analysis:**
- 84% overhead is unacceptable for latency-sensitive workloads
- Cache hits are fast (210ms) but first-request penalty is severe
- p99 at 33s suggests occasional very slow requests

**Root Causes to Investigate:**
1. BM25 search time
2. Compression pipeline overhead
3. Token counting (count_tokens calls)
4. Validation gate processing

### 6. Documentation / Onboarding (Score: 6/10)

**CLI Help:** ✓ Works

**Docs Inventory:**
- QUICKSTART.md: ✓ Solid, 7.2KB, covers proxy + SDK modes
- FAQ.md: ✓ Good, addresses common questions
- API.md: ✓ Exists
- TROUBLESHOOTING.md: ✓ Comprehensive (25KB)
- getting-started.md: Redirect stub only

**Missing:**
- ❌ No MkDocs Material setup
- ❌ No single canonical entry point (multiple READMEs)
- ❌ No visual architecture diagram in quickstart
- ❌ No "savings showcase" page with real numbers

**File count in docs/:** 77 files — sprawling, needs consolidation

## New Issues Found

1. **Fleet Unreachable** — SSH to sue@suewu and cali@calibot both timed out. Either network issue or agents down.

2. **84% Latency Overhead** — Previous estimates were 47% (140ms on 300ms call). Actual measured overhead is 567ms (84%). This is worse than expected.

3. **Low Compression Ratio** — Only 3.4% of tokens actually compressed; 87% protected. May be correct behavior (protecting important context) but worth investigating if more aggressive compression could help.

4. **Validation Soft-Blocks** — "deterministic request missing required context block" appearing in logs. Not blocking requests but indicates potential issue with request format.

## Recommendations

### P0 — Critical (fix this week)

1. **Investigate latency overhead** — Profile the compression pipeline, find the 567ms
   - File: `~/vault/01_PROJECTS/tokenpak/proxy.py`
   - Effort: 4-8 hours

2. **Fix fleet reachability** — Check SSH keys, network, agent status
   - Check: `ping suewu`, `ssh -v sue@suewu`
   - Effort: 1 hour

### P1 — High Priority

3. **Add latency breakdown to logs** — Show compression time, BM25 time, upstream time separately
   - File: `proxy.py` logging section
   - Effort: 2 hours

4. **Consolidate quickstarts** — Single entry point, archive duplicates
   - Files: `docs/QUICKSTART.md`, `README.md`, `docker-quickstart.md`
   - Effort: 2 hours

### P2 — Medium Priority

5. **Add savings showcase to docs** — Page with real fleet numbers
   - Location: `docs/SAVINGS.md`
   - Effort: 1 hour

6. **Setup MkDocs Material** — Professional doc site
   - Location: `packages/tokenpak_docs/`
   - Effort: 4 hours

### P3 — Low Priority

7. **Add `tokenpak init` wizard** — Interactive config setup
   - Effort: 4 hours

## Comparison to Previous Audit

**No previous audits found in `~/vault/01_PROJECTS/tokenpak/docs/audits/`**

This is the first automated adoptability audit. Future audits will compare against these baseline scores:
- Savings/ROI: 7
- Trust/Reliability: 6
- Ease of Use: 7
- Accuracy: 8
- Speed: 4
- Documentation: 6
- **Weighted Total: 6.4/10**

## Raw Data

### /stats endpoint
```json
{
  "session": {
    "requests": 129,
    "input_tokens": 2024929,
    "sent_input_tokens": 1956544,
    "saved_tokens": 68385,
    "protected_tokens": 1927126,
    "output_tokens": 27998,
    "cost": 7.15,
    "errors": 0,
    "cache_hits": 124,
    "cache_misses": 5
  },
  "today": {
    "requests": 6015,
    "input_tokens": 91999130,
    "output_tokens": 1332673,
    "total_cost": 276.75,
    "avg_latency_ms": 4548,
    "protected_tokens": 79998105,
    "compressed_tokens": 2813965
  }
}
```

### /health endpoint
```json
{
  "status": "ok",
  "compilation_mode": "hybrid",
  "vault_index": {"available": true, "blocks": 7938},
  "router": {"enabled": true, "components": {"slot_filler": true, "recipe_engine": true, "validation_gate": true}},
  "shadow_reader": {"enabled": true},
  "circuit_breakers": {"anthropic": {"open": false}, "openai": {"open": false}, "google": {"open": false}},
  "latency": {"p50_latency_ms": 1902, "p99_latency_ms": 33677}
}
```

### Latency Test Results
```
Direct API (haiku, unique prompt): 674ms
Proxy (haiku, unique prompt): 1241ms
Overhead: 567ms (84%)

Proxy (cached): 210ms
```

### systemctl status
```
Active: active (running) since Fri 2026-03-27 13:34:48 PDT; 27min ago
Memory: 946.6M
Errors in session: 0
```
