# Cache TTL Analysis — 2026-03-10

## Current Configuration
- TTL: **5 minutes effective** (default Anthropic prompt cache TTL)
- Source in code:
 - `tokenpak/agent/proxy/prompt_builder.py` sets `cache_control: {"type": "ephemeral"}` on stable system block(s)
 - No `ttl` field is currently set in TokenPak request shaping, so provider default applies
- Configurability in current TokenPak code: **Not exposed as a local setting today** (would require adding `ttl: "1h"` in emitted `cache_control` where appropriate)

## Anthropic Cache Behavior
- Default TTL: **5 minutes**
- Configurable: **Yes**
- Supported longer TTL: **1 hour** via `cache_control: {"type":"ephemeral", "ttl":"1h"}`
- Max TTL (per current Claude docs): **1 hour**
- Notes:
 - Cache refreshes when reused within TTL window
 - 1-hour TTL has higher cache-write pricing than 5-minute TTL

## Request Pattern Analysis (monitor.db)
Computed from `requests.timestamp` gaps between consecutive requests.

### Top rounded gap frequencies
- 0 min: 312
- 1 min: 13
- 2 min: 7
- 3 min: 3
- Long-tail outliers: 185–2078 min (single occurrences)

### Aggregate stats
- Sample size: 345 inter-request gaps
- Average gap: **12.86 min**
- Median gap: **0.10 min**
- % within 5-minute window: **97.10%**

Interpretation: traffic is highly bursty with occasional long idle periods; median and within-window rate are strong for 5-minute caching, while mean is inflated by sparse outliers.

## Recommendations
1. **Keep 5-minute TTL as default.** Current usage already lands within TTL ~97% of the time.
2. **Do not globally switch to 1-hour TTL yet.** It likely adds write cost with limited additional hit-rate gain for current pattern.
3. **Add optional TTL override feature** (e.g., env/config flag) for specific workloads with known >5m reuse patterns.
4. **Track segmented hit-rate by workflow/session type** to identify whether a subset would benefit from 1-hour TTL.

## Impact on Hit Rate
- Current expected hit rate (TTL=5m, based on inter-request timing only): **~97.1% upper-bound window eligibility**
- Achievable hit rate with 1-hour TTL (timing-only): **slightly higher, likely marginal** for current observed distribution
- Practical note: real cache hit rate also depends on prompt-prefix stability, not just timing.

## Evidence Snippets
- TokenPak cache marker placement: `tokenpak/agent/proxy/prompt_builder.py` (`cache_control: {"type":"ephemeral"}`)
- Anthropic TTL support: Claude prompt caching docs (`5-minute default`, `1-hour optional via ttl: "1h"`)
