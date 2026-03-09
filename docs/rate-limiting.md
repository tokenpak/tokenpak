# TokenPak Rate Limit Strategy

This document describes how TokenPak handles provider rate limits, the backoff strategy employed, configuration options, and monitoring guidance.

---

## A. How Rate Limits Work Per Provider

### OpenAI

OpenAI enforces two primary limit dimensions: **Tokens Per Minute (TPM)** and **Requests Per Minute (RPM)**. These limits vary per model — GPT-4 class models have stricter TPM ceilings than GPT-3.5 variants. When a limit is hit, OpenAI returns HTTP 429 with a `Retry-After` header indicating how many seconds to wait before the next attempt. The `x-ratelimit-remaining-requests` and `x-ratelimit-remaining-tokens` response headers can be monitored proactively to slow down before hitting the wall. Tier-based accounts have higher limits (Tier 1–5), and per-project limits can be configured in the OpenAI dashboard. Burst capacity allows short-term spikes, but sustained overuse always triggers 429s.

**Detection:** HTTP 429 response; `error.type == "requests" or "tokens"` in the JSON body.
**Reset:** Typically 1-minute rolling window; check `x-ratelimit-reset-requests` and `x-ratelimit-reset-tokens` headers.

### Anthropic

Anthropic enforces **Tokens Per Minute (TPM)** and **requests per minute (RPM)** with a concurrent-request limit layered on top. Unlike OpenAI, Anthropic's limits are tied to API key tiers and can be increased by request. Claude models share a per-key pool, so heavy parallel usage from a single key will hit concurrency limits before hitting TPM. The `anthropic-ratelimit-requests-remaining` and `anthropic-ratelimit-tokens-remaining` headers are included in every response for proactive monitoring.

**Detection:** HTTP 429; `error.type == "rate_limit_error"` in the response body.
**Reset:** Per-minute rolling window; `anthropic-ratelimit-requests-reset` header gives the UTC reset timestamp.

### Google Vertex AI / Generative AI

Google uses a **quota system** that covers Tokens Per Minute, Requests Per Minute, and daily quota caps. Vertex AI limits are per-project and per-region. Google's 429s include a `Retry-After` or `retry_delay` in the error body. Unlike OpenAI/Anthropic, Google's quota resets can be partial — hitting the daily cap requires waiting until midnight Pacific time or filing a quota increase request. Rate limits differ between `us-central1` and other regions, so multi-region routing can help distribute load.

**Detection:** HTTP 429; gRPC `RESOURCE_EXHAUSTED` code; `error.status == "RESOURCE_EXHAUSTED"` in JSON.
**Reset:** Per-minute rolling window (RPM/TPM); daily quota resets at midnight Pacific.

---

## B. Backoff Strategy

TokenPak uses **exponential backoff with jitter** as the primary strategy, implemented in `tokenpak/handlers/rate_limit.py` (`RateLimitBackoff`) and `tokenpak/agent/agentic/retry.py` (`RetryEngine`).

### Core Formula

```
delay = min(base_delay * (2 ^ attempt), max_delay)
jitter = random(0, delay * jitter_factor)
actual_wait = delay + jitter
```

Full jitter prevents thundering-herd problems when multiple TokenPak instances simultaneously hit a rate limit and back off in sync.

### Retry-After Header

When a provider includes a `Retry-After` header, it takes priority over the computed exponential delay (capped at `max_delay`). This is faster than waiting longer than necessary.

### Circuit Breaker

The `RetryEngine` implements a 5-level escalation circuit breaker:

- **Level 0:** Exponential backoff retries (configured `wait_seconds` list)
- **Level 1:** Model downgrade (e.g., Opus → Sonnet → Haiku) — cheaper models often have higher rate limits
- **Level 2:** Provider switch (Anthropic → OpenAI → Google)
- **Level 3:** Agent handoff — preserve state, hand off to another agent
- **Level 4:** Save partial state + alert human; raise `RetryExhaustedError`

Auth errors (401, 403) bypass the escalation chain and jump directly to Level 4 — no point retrying unauthenticated requests.

### Per-Provider Tuning

- **OpenAI:** Shorter initial delay (500ms), more retries (5) — OpenAI's limits reset every minute, so short bursts of retries are effective
- **Anthropic:** Standard delay (1000ms), 3 retries — Anthropic's limits are generous; if you're hitting them, the problem is concurrency, not throughput
- **Google:** Longer initial delay (2000ms), 4 retries — Google's quota system is slower to reset and daily caps require a longer wait strategy

---

## C. Configuration

TokenPak loads rate limit configuration from `~/tokenpak/config/rate-limits.yaml`. The full schema:

```yaml
rate_limiting:
  enabled: true
  strategies:
    default:
      name: exponential_backoff
      base_delay_ms: 1000
      max_delay_ms: 60000
      exponential_base: 2.0
      jitter_factor: 0.1
      max_retries: 3

    openai:
      base_delay_ms: 500
      exponential_base: 1.5
      max_retries: 5

    anthropic:
      base_delay_ms: 1000
      max_retries: 3

    google:
      base_delay_ms: 2000
      max_retries: 4

  alerts:
    enabled: true
    thresholds:
      rate_limit_events_per_hour: 5
      backoff_duration_seconds: 300
```

The `RetryEngine` also reads `~/.tokenpak/config.json` for `retry.wait_seconds`, `retry.downgrade_chain`, and `retry.per_error` keys, which take precedence over defaults but are overridden by caller-supplied parameters.

---

## D. Monitoring & Alerts

### Event Logging

Every rate limit event is appended to `~/.tokenpak/retry_events.jsonl` via `_append_retry_event()`. Each entry includes:
- `event` type (e.g., `level0_retry`, `level1_model_downgrade`, `level4_human_alert`)
- `task_id` and `agent` identifier
- `http_status` that triggered the event
- ISO timestamp

Use `tokenpak.agent.agentic.retry.load_recent_retry_events(n=20)` to pull the last N events programmatically.

### Alert Thresholds

| Metric | Warning Threshold | Action |
|--------|-------------------|--------|
| Rate limit events/hour | > 5 | Investigate load patterns; consider request throttling upstream |
| Backoff duration | > 300s cumulative | Model or provider is saturated; escalate or redistribute load |
| Level 4 alerts | Any | Immediate human intervention required |

### Key Metrics to Monitor

- **Rate limit frequency by provider:** Are you hitting one provider disproportionately?
- **Average backoff duration:** High averages indicate persistent saturation
- **Model downgrade rate (Level 1):** Frequent downgrades signal sustained over-limit conditions
- **Provider switch rate (Level 2):** Should be rare; if frequent, primary provider needs quota increase
- **RetryExhaustedError count:** Any non-zero value is a production incident

### Dashboard Items

1. `retry_events.jsonl` rolling 1-hour event count (alert if > 5)
2. Level 0 → 4 escalation distribution (pie chart)
3. Per-provider 429 frequency over time (line chart)
4. Average successful backoff wait time (gauge)
5. RetryExhaustedError count (alert on non-zero)
