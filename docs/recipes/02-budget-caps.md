# Recipe: Budget Caps & Spend Alerts

**What this solves:** Set a daily or monthly spending limit and automatically reject requests when approaching or exceeding the cap.

## Prerequisites
- TokenPak installed with `budget` plugin enabled
- A billing API key or webhook endpoint to track spend
- Access to logs or monitoring system for alerts
- Valid API keys for your providers

## Config Snippet

```yaml
# config.yaml
budget:
  enabled: true
  # Daily cap: $10.00 USD
  daily_limit_cents: 1000
  # Monthly cap: $200.00 USD
  monthly_limit_cents: 20000

  # Alert when spend reaches 80% of cap
  alert_threshold_pct: 80
  alert_webhook: https://monitoring.example.com/budget-alert

  # Action when limit exceeded
  reject_on_exceed: true
  # Optional: graceful degradation (route to cheaper model instead)
  fallback_model_on_exceed: gpt-3.5-turbo

  # Track spend per user (optional)
  per_user_limits:
    enabled: true
    user_limits:
      user-a:
        daily_limit_cents: 300
      user-b:
        daily_limit_cents: 500

providers:
  openai:
    type: openai
    api_key: ${OPENAI_API_KEY}

models:
  gpt-4:
    provider: openai
    estimated_input_cost_per_1k: 3  # cents
    estimated_output_cost_per_1k: 6  # cents

  gpt-3.5-turbo:
    provider: openai
    estimated_input_cost_per_1k: 0.5
    estimated_output_cost_per_1k: 1.5
```

## Test & Verify

**Step 1:** Validate the config:
```bash
tokenpak validate-config config.yaml
# Expected output:
# ✓ Config valid
# ✓ Budget tracking enabled (daily: $10.00, monthly: $200.00)
# ✓ Per-user limits: user-a ($3.00/day), user-b ($5.00/day)
```

**Step 2:** Start the proxy and make requests:
```bash
export OPENAI_API_KEY="sk-real-key"
tokenpak proxy --config config.yaml

# In another terminal, make 5 requests to consume budget
for i in {1..5}; do
  curl -X POST http://localhost:8000/v1/messages \
    -H "Authorization: Bearer user-a" \
    -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "Write 100 words of code"}]}' \
    -s | jq '.cost_cents'
done
# Expected output (cumulative):
# 180
# 360
# 540
# 720
# 900  <- This triggers 80% alert
```

**Step 3:** Verify alert was sent:
```bash
# Check logs for webhook call
tail -f /var/log/tokenpak/alerts.log | grep "budget.*threshold"
# Expected output:
# 2026-03-25T09:45:00Z ALERT budget_threshold_exceeded user=user-a spent=$9.00 daily_limit=$3.00 pct=300%
```

**Step 4:** Exceed the limit and verify rejection:
```bash
# Make one more request (would exceed daily limit)
curl -X POST http://localhost:8000/v1/messages \
  -H "Authorization: Bearer user-a" \
  -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "One more request"}]}' \
  -s | jq '.error'
# Expected output:
# {
#   "code": "budget_exceeded",
#   "message": "User user-a has reached daily budget limit ($3.00). Rejecting request.",
#   "daily_spent": "$9.00",
#   "daily_limit": "$3.00"
# }
```

## What Just Happened

TokenPak maintained a running cost tally for each request:

1. **Request arrives** with user `user-a` requesting `gpt-4`
2. **Cost estimation** based on model pricing and estimated token count
3. **Spend check** against `user-a`'s daily limit ($3.00)
4. **When 80% reached** ($2.40), webhook notification sent to your monitoring system
5. **When limit exceeded** ($3.00+), subsequent requests rejected with `budget_exceeded` error

Users are aware of budget constraints without application code changes — TokenPak enforces them transparently.

## Common Pitfalls

**Pitfall 1: Cost estimates are inaccurate**
- ❌ Wrong: Hardcoded fixed costs that don't match actual provider pricing
- ✅ Right: Query provider APIs hourly for latest pricing: `tokenpak sync-pricing --provider openai`

**Pitfall 2: Alert webhook is unreliable**
- ❌ Wrong: Single webhook with no retry logic, silent failures
- ✅ Right: Configure multiple webhooks or fallback to email: `alert_webhook: [https://..., mailto://ops@...`]

**Pitfall 3: Per-user limits are too permissive**
- ❌ Wrong: Set limits equal to total budget (allows one user to consume everything)
- ✅ Right: Distribute: `total_budget / num_users * 1.2` with monitoring per user

**Pitfall 4: Forgetting to account for overage**
- ❌ Wrong: Set monthly limit to $100, but requests are blocking at $100 exactly
- ✅ Right: Set limit to `$100 - (estimated_max_single_request * 2)` to allow completion

**Pitfall 5: Cost tracking falls out of sync**
- ❌ Wrong: Estimate-based tracking drifts from actual billing
- ✅ Right: Hourly reconciliation with actual provider invoices: `tokenpak reconcile-spend --provider openai`
