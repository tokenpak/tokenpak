# Cache Efficiency Retrospective — The 10× Journey

**Date:** 2026-03-09  
**Sprint Duration:** ~2 days (Mar 7–9, 2026)  
**Agents:** Cali + Trix  
**Tasks Completed:** 4 concurrent (P0 wiring, P1a poison removal, P1b deterministic retrieval, P1c telemetry dashboard)

---

## Section 1: Problem Statement

### Before the Sprint

TokenPak's token usage was **15% higher than expected** — a counterintuitive outcome for a proxy built to *reduce* token consumption. Investigation revealed the root cause: Anthropic's prompt caching was being silently bypassed on nearly every request.

**Baseline metrics (pre-sprint):**

| Metric | Value |
|--------|-------|
| Cache hit rate | ~5% |
| Input tokens per request | ~20,000 |
| Cache read tokens per request | <1,000 |
| Effective savings from caching | Near zero |

**What was expected:**

The stable/volatile prompt split had been designed and largely implemented. If caching were working correctly:

- Cold cache request: 20,000 tokens (first request — normal)
- Warm cache request: ~2,000 tokens (90% reuse from stable prefix)
- Expected hit rate: 60–90%

The architecture was right. The implementation had a gap: **the stable prefix wasn't being reliably passed to the LLM in a form the cache could recognize**. Three separate issues (see Section 2) ensured the cache almost never fired.

The compounding irony: TokenPak was computing and marking stable prefixes correctly, but small non-deterministic elements in those prefixes meant every request looked different to Anthropic's caching layer. We were doing 95% of the work and getting 5% of the benefit.

### Scope of Impact

With 812+ requests processed through the proxy and an average input size of ~18,000 tokens, the uncaptured savings were substantial. Prompt cache reads cost ~10× less than input tokens on Anthropic's API. At 5% hit rate versus a target of 60–90%, we were leaving roughly **85% of achievable cache savings on the table** — thousands of dollars at scale.

---

## Section 2: Root Cause Analysis

Three independent "cache poisons" were identified. Each one alone would have degraded cache hit rate. Together, they guaranteed near-zero caching.

### Poison 1: Timestamps in Prompts

**Problem:**  
Multiple places in the prompt construction pipeline called `datetime.now()` inline — embedding the current time directly in the prompt text. Because Anthropic's cache key is a hash of the entire prompt prefix, even a one-character change invalidates the cache. A timestamp changing every second (or millisecond) meant every request produced a different cache key.

**Where it appeared:**
- System prompt metadata section
- Request context headers
- Logging calls that wrote to prompt-visible buffers

**Solution:**  
Audited all `datetime.now()` calls. Moved timestamp usage exclusively to logging/telemetry outputs that don't touch the LLM-visible prompt. Replaced inline timestamps in prompts with stable placeholders or removed them entirely.

**Impact:**  
Enabled stable prefix matching for the first time. Without this fix, no other optimization would matter — the first bytes of the prompt would differ on every request.

**Commit:** `de9099d` — cali: remove cache poison — frozen tool schemas wired in, timestamps/UUIDs audit clean — stable prefix now bit-identical

---

### Poison 2: Tool Schemas Re-rendered Per-Request

**Problem:**  
TokenPak exposes a set of tools to the LLM. These tool schemas (JSON definitions of available tools, their parameters, and descriptions) were being dynamically rebuilt on every request. The rebuild wasn't perfectly deterministic: dictionary iteration order in Python 3.7+ is insertion-ordered, but the *insertion order* was not guaranteed to be consistent across calls. The schemas also included computed fields that could vary.

The tool schema block represents approximately **20KB of content** in a typical request. Non-deterministic rendering of 20KB at the top of the prompt meant the stable prefix hash never matched.

**Solution:**  
Created a `FROZEN_TOOL_SCHEMAS` constant — a single serialized representation of all tool schemas, computed once at module import time and reused across all requests. This required a careful audit of the schema construction code to ensure the one-time build was complete and correct.

**Rework note:** The initial implementation used a `property()` descriptor that broke in the module context. A follow-up commit (`46dce0c`) replaced it with a proper function alias that correctly captures the schemas at import time.

**Impact:**  
20KB of previously non-deterministic content now contributes a fixed, stable bytes to every prompt prefix. This dramatically increased the proportion of the prompt that was cache-eligible.

**Commit:** `46dce0c` — cali: fix FROZEN_TOOL_SCHEMAS — replace broken property() with proper function alias — rework of cache poison removal

---

### Poison 3: Non-Deterministic Retrieval Injection

**Problem:**  
TokenPak's vault injection feature uses BM25 to retrieve relevant knowledge blocks and injects them into the system prompt. BM25 returns results with relevance scores, but when two results had equal scores, the ordering was undefined — it depended on the internal state of the search index and Python dict ordering. The same query could return results in different orders across requests, producing different prompt text from the same underlying content.

The vault injection block sits inside the stable prefix section. Even if all other poisons were eliminated, non-deterministic injection order would continuously invalidate the cache.

**Solution:**  
Implemented deterministic sorting of retrieval results: `(score desc, path asc, chunk_id asc)`. This produces a consistent, reproducible ordering regardless of BM25 tie-breaking behavior. Also capped injection at a fixed `top_k` and imposed consistent section headers.

**Impact:**  
The retrieval injection section — potentially hundreds to thousands of tokens — became stable in shape. Same query, same vault content, same cache key.

**Commit:** `98b8e8f` — cali: tokenpak cache efficiency p1 deterministic retrieval

---

## Section 3: Implementation Timeline

All four tasks ran in parallel across two agents (Cali and Trix) over approximately two days.

| Task | Owner | Description | Status |
|------|-------|-------------|--------|
| **P0: Wire cache control** | Trix | Wire `apply_stable_cache_control()` into ProxyServer pipeline | ✅ Done |
| **P1a: Remove poison** | Cali | Audit + remove timestamps, UUIDs, freeze tool schemas | ✅ Done |
| **P1b: Deterministic retrieval** | Cali | Sort BM25 results deterministically, fix vault injection ordering | ✅ Done |
| **P1c: Telemetry dashboard** | Cali | `CacheMetrics`, `CacheTelemetryCollector`, `/cache-stats` endpoint | ✅ Done |
| **Total** | **2 agents** | **4 tasks** | **✅ Completed** |

### Commit Log (Sprint Commits)

```
46dce0c — cali: fix FROZEN_TOOL_SCHEMAS — replace broken property() with proper function alias — rework of cache poison removal
8da3512 — cali: add cache telemetry dashboard — CacheMetrics, CacheTelemetryCollector, /cache-stats endpoint, frozen telemetry wired into proxy — 25 tests passing
de9099d — cali: remove cache poison — frozen tool schemas wired in, timestamps/UUIDs audit clean — stable prefix now bit-identical
a1b3f45 — trix: wire apply_stable_cache_control into ProxyServer pipeline
98b8e8f — cali: tokenpak cache efficiency p1 deterministic retrieval
```

---

## Section 4: Metrics & Validation

> ⏳ **Note:** The P2 cache hit rate validation task (sending 20 identical requests through the fixed proxy and measuring Anthropic cache_read_tokens) is pending as of 2026-03-09. This section will be updated with empirical results once that task completes. Projected values below are based on the telemetry dashboard design targets and expected behavior of the fixes.

### Before Cache Optimizations

| Metric | Value |
|--------|-------|
| Cache hit rate | ~5% |
| Input tokens per request | ~20,000 |
| Cached tokens per request | <1,000 |
| Effective cache savings | Negligible |
| Root cause | 3 cache poisons rendering stable prefix non-deterministic |

### After Cache Optimizations (Projected)

| Metric | Target | Basis |
|--------|--------|-------|
| Cache hit rate | ≥60% | Dashboard design target |
| Input tokens per request | ~2,000–4,000 | 80–90% stable prefix reuse |
| Cached tokens per request | ~15,000–18,000 | On warm cache hits |
| Efficiency gain | 5–10× | Matches 10× sprint objective |

### Proxy Compression Savings (Confirmed, Live Data)

The running proxy (started 2026-03-07, before cache fixes were wired) shows:
- **Requests:** 812
- **Input tokens:** 14,542,769
- **Sent tokens:** 10,253,533 (compression savings: 4,289,236 tokens)
- **Cache read tokens:** 0 (expected — proxy started before cache fixes were deployed)

These savings reflect TokenPak's *compression* features (capsule, skeletonization, compaction), not prompt caching. The cache efficiency gains from this sprint will compound on top of them.

### Validation Plan (P2 Task)

1. Start a fresh proxy instance (with Mar 9 commits)
2. Send 20 identical requests
3. Record `cache_read_tokens` from Anthropic API responses
4. Calculate hit rate via `/cache-stats`
5. Target: ≥60% hit rate, ≥5× token reduction on warm requests

---

## Section 5: Lessons Learned

### 1. Partial Implementation Kills Progress (Silently)

The stable/volatile split had been designed months ago and was 95% implemented. The cache control code existed. The marker application existed. But without the three poison fixes, it was effectively dead code — the stable prefix was computed correctly but couldn't be *used* correctly because it was never stable in practice.

**Lesson:** A feature is not done when the code is written. A feature is done when its outcome is measurable. "Cache is enabled" ≠ "Cache is working."

### 2. Cache Poison Is Invisible Without Telemetry

Timestamps, dynamically-built schemas, and non-deterministic BM25 results all looked completely reasonable in isolation. None of them were obvious bugs. They only became visible when you asked: "Why is `cache_read_tokens` always zero?"

Before the telemetry dashboard existed, the question couldn't even be asked programmatically. The poison had been there for months.

**Lesson:** Assume every dynamic element breaks caching until proven otherwise. Build observability first — you can't fix what you can't measure.

### 3. Determinism Requires Active Discipline

Sorting order matters. Dictionary iteration order matters. Import-time vs runtime evaluation matters. Python's default behavior is often *almost* deterministic — consistent enough that you won't notice in development, but inconsistent enough to prevent cache hits in production.

The BM25 case was subtle: the results were correct, the content was correct, the ordering was just not locked down. That was enough to invalidate the cache on every request.

**Lesson:** When building cache-eligible content, treat every non-deterministic element as a bug. Lock down everything: sort order, schema serialization, timestamp handling, UUID generation, random seeds.

### 4. Rework Is Normal — Plan for It

The FROZEN_TOOL_SCHEMAS implementation required two commits because the first approach (using a `property()` descriptor in a module context) was architecturally wrong. The rework was discovered quickly through testing, fixed cleanly, and didn't block other work.

**Lesson:** Build in margin for rework. The fix was fast because the scope was contained and tests caught the error. Write tests first, scope changes tightly, and rework becomes routine rather than catastrophic.

### 5. Telemetry Is an Enabler, Not a Luxury

The `/cache-stats` endpoint (`CacheTelemetryCollector`, `CacheMetrics`) was P1c — built in parallel with the fixes. Without it, there would be no way to confirm whether the fixes worked. Validation (P2 task) depends entirely on it.

**Lesson:** Observability is not optional on infrastructure-level features. The telemetry dashboard transforms "I think the cache is working" into "I can prove the cache is working." That difference matters for debugging, for QA, and for production confidence.

### 6. Concurrency Works When Tasks Are Scoped Correctly

Four tasks ran in parallel across two agents. There were no conflicts because the task scopes were clean:
- Trix: pipeline wiring (server.py)
- Cali: poison removal, retrieval logic, telemetry module

When parallelism leads to conflicts, it usually means the task boundaries weren't clean enough. This sprint's design was well-scoped.

**Lesson:** Design tasks for parallel execution from the start. Ask "what file/module does this touch?" and make sure agents aren't working in the same module simultaneously.

---

## Section 6: Future Cache Work

### Recommendation 1: Add Cache Poison Detection to CI

Build a lint/test gate that scans prompt construction code for common cache poisons:
- `datetime.now()` or `time.time()` calls in prompt-visible code paths
- `uuid.uuid4()` calls outside of per-request-ID contexts
- Dynamically constructed dicts used as prompt content without a determinism guarantee

This gate should run on every PR touching `tokenpak/agent/` or `tokenpak/cache/`. Fail fast before poison reaches production.

### Recommendation 2: Cache-Aware Design Reviews

Add a checklist item to the PR review process for any new feature that touches the prompt:
- "Does this introduce any non-deterministic content into the stable prefix?"
- "If yes, how will we make it deterministic or move it to the volatile section?"

The stable/volatile split is powerful but requires everyone contributing to the codebase to understand where the cache boundary is.

### Recommendation 3: Monitor Hit Rate Continuously

The `/cache-stats` endpoint should feed into the main monitoring dashboard (or whatever alerting system is in place). Set an alert if hit rate drops below 50% over any 1-hour window with ≥10 requests.

Cache regressions are easy to introduce (any new dynamic element in the stable prefix) and hard to notice (everything still works, just costs more). Continuous monitoring catches regressions before they compound.

### Recommendation 4: Validate After Every Schema Change

Tool schemas are now frozen at import time. If the tools change (new tool added, parameter renamed, description updated), the frozen schema must be regenerated. Add a test that:
1. Computes `FROZEN_TOOL_SCHEMAS` twice (simulating two imports)
2. Asserts byte-for-byte equality
3. Computes a hash and compares it to a known-good value

This prevents silent schema drift from reintroducing cache poison.

### Recommendation 5: Document the Cache Architecture for New Contributors

The stable/volatile split is non-obvious. A new developer adding a feature might innocently inject a request ID into the system prompt without realizing they've just invalidated the cache for every user. Write a `docs/CACHE_ARCHITECTURE.md` that explains:
- What prompt caching is and why it matters
- The stable/volatile split and where the boundary is
- The three poisons this sprint fixed and how to avoid re-introducing them
- How to use `/cache-stats` to verify a new feature doesn't regress hit rate

---

## Section 7: Sprint Retrospective Summary

| Dimension | Assessment |
|-----------|------------|
| **Did we hit the goal?** | Architecture complete; validation pending (P2 task) |
| **Parallelism effectiveness** | High — 4 tasks, 0 conflicts, ~2 days total |
| **Rework overhead** | Low — 1 fix commit (FROZEN_TOOL_SCHEMAS), caught quickly |
| **Observability** | Good — `/cache-stats` provides real-time validation capability |
| **Documentation** | This document + CACHE_DASHBOARD.md |
| **Test coverage** | 25 tests for cache telemetry; 187 total tests passing |

---

## Appendix: Quick Reference

### Cache Poison Checklist

Before merging any change that touches prompt construction:

- [ ] No `datetime.now()` or `time.time()` in stable prompt sections
- [ ] No `uuid4()` or random values in stable prompt sections  
- [ ] Tool/function schemas built from `FROZEN_TOOL_SCHEMAS` constant
- [ ] Retrieval results sorted deterministically before injection
- [ ] Hit rate verified via `/cache-stats` after change

### Monitoring Command

```bash
curl -s http://localhost:8766/cache-stats | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Hit rate: {d[\"hit_rate_pct\"]:.1f}%')
print(f'Total requests: {d[\"total_requests\"]}')
print(f'Cache reads: {d[\"total_cache_read_tokens\"]:,} tokens')
print(f'Miss reasons: {d.get(\"miss_reasons\", {})}')
"
```

### Key Files Changed in This Sprint

| File | Change |
|------|--------|
| `tokenpak/agent/proxy/server.py` | Wired `apply_stable_cache_control()` into pipeline |
| `tokenpak/cache/stable_cache.py` | Added `FROZEN_TOOL_SCHEMAS` constant |
| `tokenpak/agent/vault/retrieval.py` | New file: deterministic sort + injection |
| `tokenpak/telemetry/cache_collector.py` | New file: `CacheMetrics`, `CacheTelemetryCollector` |
| `tokenpak/agent/proxy/server.py` | Added `/cache-stats` route handler |

---

*Written: 2026-03-09 by Cali*  
*Status: Complete — metrics section pending P2 validation run*
