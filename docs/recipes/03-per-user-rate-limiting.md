# Recipe: Per-User Rate Limiting

**What this solves:** Assign different rate limits (requests/minute) to different users so power users aren't throttled by casual users' consumption.

## Prerequisites
- TokenPak installed with `rate-limit` plugin enabled
- User authentication configured (API keys or JWT tokens)
- Understanding of your expected usage pattern (requests per minute per user tier)

## Config Snippet

```yaml
# config.yaml
rate_limit:
  enabled: true
  # Global default: 10 requests per minute
  default_rps: 10

  # Per-user tiers with different limits
  user_tiers:
    basic:
      rps: 5  # Basic: 5 req/min
      burst: 2  # Allow 2 extra in a burst
      window_seconds: 60

    standard:
      rps: 50  # Standard: 50 req/min
      burst: 10
      window_seconds: 60

    premium:
      rps: 500  # Premium: 500 req/min (unlimited in practice)
      burst: 100
      window_seconds: 60

  # Map users to tiers
  user_assignments:
    user-123:
      tier: basic
    user-456:
      tier: standard
    user-789:
      tier: premium

  # Enforcement behavior
  enforce: reject  # Options: reject, queue, degrade
  # reject: return 429 Too Many Requests
  # queue: hold request, process when rate available
  # degrade: route to cheaper model if rate exceeded

providers:
  openai:
    type: openai
    api_key: ${OPENAI_API_KEY}

models:
  gpt-4: { provider: openai }
  gpt-3.5-turbo: { provider: openai }
```

## Test & Verify

**Step 1:** Validate config:
```bash
tokenpak validate-config config.yaml
# Expected output:
# ✓ Config valid
# ✓ Rate limiting: 3 tiers configured (basic/standard/premium)
# ✓ User assignments: 3 users (user-123→basic, user-456→standard, user-789→premium)
```

**Step 2:** Start proxy and test basic tier user:
```bash
tokenpak proxy --config config.yaml

# In another terminal, simulate 6 requests from a basic-tier user
for i in {1..6}; do
  curl -X POST http://localhost:8000/v1/messages \
    -H "Authorization: Bearer user-123" \
    -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "Hi"}]}' \
    -s | jq '{status: .status, cost: .cost_cents}'
done

# Expected output:
# ✓ Request 1-5: { status: 200, cost: 15 }
# ✗ Request 6: { status: 429, message: "Rate limit exceeded. Basic tier: 5 req/min" }
```

**Step 3:** Test standard tier user (higher limit):
```bash
# Same 6 requests from standard-tier user
for i in {1..6}; do
  curl -X POST http://localhost:8000/v1/messages \
    -H "Authorization: Bearer user-456" \
    -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "Hi"}]}' \
    -s | jq '{status: .status}'
done

# Expected output:
# ✓ Request 1-6: { status: 200 }
```

**Step 4:** Verify per-user isolation (different windows don't interfere):
```bash
# Hammer basic tier user, then check standard tier is unaffected
ab -n 50 -c 5 -H "Authorization: Bearer user-123" \
  -p request.json http://localhost:8000/v1/messages
# Results: ~5 success, ~45 rejected (429)

# Standard tier user still has full quota
curl -X POST http://localhost:8000/v1/messages \
  -H "Authorization: Bearer user-456" \
  -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "Still works?"}]}' \
  -s | jq '.status'
# Expected output: 200
```

## What Just Happened

TokenPak tracked each user independently:

1. **Request arrives** with `Authorization: Bearer user-123`
2. **User lookup** in `user_assignments` → tier is `basic`
3. **Rate limit check** against `basic` tier's `5 rps` window
4. **If within limit**: request processed normally
5. **If exceeded**: request rejected with `429 Too Many Requests`

Each user has their own request counter, resetting every 60 seconds. Users cannot starve each other — high-volume power users operate within their tier limit independently.

## Common Pitfalls

**Pitfall 1: Rate limits are too uniform**
- ❌ Wrong: All users get 10 req/min regardless of tier
- ✅ Right: Differentiate clearly: basic=5, standard=50, premium=500

**Pitfall 2: Burst allowance is missing**
- ❌ Wrong: Reject immediately on 6th request (no flexibility)
- ✅ Right: Allow small bursts: `burst: 2` to handle traffic spikes

**Pitfall 3: User tier assignment is stale**
- ❌ Wrong: Hardcoded tiers that don't update when user upgrades
- ✅ Right: Query billing system on every request: `user_tier_source: https://api.example.com/user-tier/{user_id}`

**Pitfall 4: Window reset is unpredictable**
- ❌ Wrong: Sliding window (60-second window, resets every second) — hard to predict
- ✅ Right: Fixed windows (resets every minute, hour at 00:00 UTC) — easier to reason about

**Pitfall 5: Burst is too large**
- ❌ Wrong: `rps: 5, burst: 20` — allows basic user to explode with 25 requests
- ✅ Right: `rps: 5, burst: 2` — small buffer for legitimate traffic spikes, not abuse
