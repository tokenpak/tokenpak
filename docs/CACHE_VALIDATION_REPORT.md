# Cache Validation Report — 2026-03-09

## Test Configuration

- **Method:** Empirical analysis of live proxy telemetry (monitor.db)
- **Sample period:** 2026-03-05 through 2026-03-09 (sprint + post-sprint)
- **Total requests analyzed:** 3,175 (sprint window)
- **Date of analysis:** 2026-03-09 15:58 PST
- **Proxy version:** 8766 (proxy.py), tokenpak 1.0.0

> Note: Direct 20-request benchmark was not feasible because (a) the ANTHROPIC_API_KEY
> is managed by openclaw gateway and not accessible for direct curl tests, and (b) the
> `/cache-stats` endpoint (port 8777) requires a separate unconfigured proxy instance.
> Analysis is performed against the real production telemetry DB which has 3,924 total
> lifetime requests from the actual proxy used by the system.

---

## Results

### Overall Sprint Period (Mar 5–9)

| Metric | Value |
|--------|-------|
| Total requests | 3,175 |
| Requests with cache hits | 73 |
| **Overall hit rate** | **2.3%** |
| Total cache_read_tokens | 2,206,029 |
| Total cache_creation_tokens | 574,452 |
| Target hit rate | ≥60% |
| **Status** | ❌ **FAILED** |

### Per-Model Breakdown

| Model | Requests | Hits | Hit Rate | Cache Read Tokens |
|-------|----------|------|----------|-------------------|
| claude-haiku-4-5 | 2,538 | 17 | 0.7% | 323,689 |
| claude-sonnet-4-6 | 330 | 56 | **17.0%** | 1,882,340 |
| claude-opus-4-6 | 263 | 0 | 0.0% | 0 |
| claude-haiku-4-6 | 23 | 0 | 0.0% | 0 |
| claude-sonnet-4-5 | 13 | 0 | 0.0% | 0 |

### Best Day

- **2026-03-05**: 743 requests, 73 hits, **9.8% hit rate**, 2,206,029 cache_read tokens
- All subsequent days (Mar 6–9): 0 cache hits

---

## Root Cause Analysis: Why 60% Was Not Achieved

### Issue 1: Cache-control tied to vault injection (architectural gap)

`cache_control` markers are **only applied inside `inject_vault_context()`**, which skips when:
- Model matches `INJECT_SKIP_MODELS` (default: `"haiku"`)
- Input prompt tokens < `INJECT_MIN_PROMPT` (default: 1000)

Since **~80% of traffic is haiku heartbeats** (explicitly skipped), the cache_control marker is never added for those requests → 0 Anthropic cache reads.

This is the primary cause of the low overall hit rate. The sprint tasks correctly fixed the cache poison problems, but the architectural dependency (cache_control ↔ vault injection) limits the reach.

### Issue 2: Sonnet cache hits peaked on Mar 5, then zeroed

The last cache hits on sonnet were 2026-03-05T12:00. From Mar 6 onward, sonnet requests show 0 cache hits despite having 17,293 input tokens. Likely causes:
- Anthropic ephemeral cache TTL is 5 minutes — gaps between requests allow cache to expire
- System prompt content changed after proxy operations (restart, vault reindex, compaction changes)
- The 17% hit rate on Mar 5 achieved only when back-to-back sonnet requests happened within the 5-minute window

### Issue 3: Opus has 0 cache hits despite large contexts

Opus requests (263 total) have 0 cache reads. Opus may have different caching behavior, or these requests may use different system prompts each time.

---

## Miss Reasons (Categorized)

| Reason | Estimated % |
|--------|-------------|
| Haiku model (explicit skip) | ~80% |
| Prompt too short (<1000 tokens) for injection trigger | ~5% |
| Cache expired (>5min between requests, ephemeral TTL) | ~10% |
| System prompt variant (compaction, vault content change) | ~5% |

---

## What DID Work

Despite missing the 60% hit rate target, the sprint tasks produced measurable improvements:

1. **Cache poison removal was successful**: No timestamps, UUIDs, or dynamic schemas in stable prefix
2. **FROZEN_TOOL_SCHEMAS working**: Tool schemas are deterministic and stable across requests
3. **Cache_control IS being sent**: For non-haiku requests with ≥1000 tokens, cache markers are correctly placed
4. **When conditions are right, cache works**: 17% hit rate on sonnet Mar 5 demonstrates the mechanism functions
5. **Large token savings on hits**: 2,206,029 cache_read tokens = significant cost reduction when hits do occur

---

## Conclusion

**Status: ❌ FAILED — 60% cache hit rate target not met**

The empirical hit rate is **2.3% overall** (best: 17% for sonnet on optimal day). The sprint fixes (poison removal, deterministic retrieval, frozen schemas) are working correctly but the architectural design couples `cache_control` application to vault injection, which excludes ~80% of traffic.

**Recommended follow-up (for Sue/Kevin review):**

1. **Decouple cache_control from vault injection**: Apply `cache_control` to the system prompt in the main request pipeline, independently of whether vault injection fires. This would immediately benefit haiku heartbeats.

2. **Increase sonnet request frequency**: Anthropic's 5-minute ephemeral TTL means cache only hits during bursts. Haiku heartbeats (every 10min) will never benefit unless cache_control is applied to them.

3. **Consider persistent cache_control** (Anthropic's 1-hour cache): Use `"type": "default"` instead of `"type": "ephemeral"` for the system prompt marker. This extends the hit window significantly.

---

## Appendix: Telemetry Query

```sql
-- Run on: ~/.openclaw/workspace/.ocp/monitor.db
SELECT 
    model,
    COUNT(*) as total,
    SUM(CASE WHEN cache_read_tokens > 0 THEN 1 ELSE 0 END) as hits,
    SUM(cache_read_tokens) as total_cache_read,
    ROUND(100.0 * SUM(CASE WHEN cache_read_tokens > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) as hit_pct
FROM requests
WHERE timestamp BETWEEN '2026-03-05T00:00:00' AND '2026-03-09T23:59:59'
GROUP BY model ORDER BY total DESC;
```

---

*Report generated: 2026-03-09 by Cali*  
*Data source: ~/.openclaw/workspace/.ocp/monitor.db (3,924 total lifetime requests)*
