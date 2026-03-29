# Recipe: Local Development with Mock Provider

**What this solves:** Use a mock/stub provider in development to avoid API costs and rate limits while testing, then switch to real providers in production with zero code changes.

## Prerequisites
- TokenPak installed
- Python or local environment for testing
- Understanding of mock responses (deterministic, predictable)

## Config Snippet

```yaml
# config.yaml (local development)
providers:
  # Mock provider: responds instantly with fake data
  mock:
    type: mock
    # Mock responses follow patterns:
    # - latency: fake delay (simulate real provider)
    # - deterministic: same input = same output
    latency_ms: 200  # Simulate 200ms API latency

    # Canned responses (by model)
    responses:
      gpt-4:
        default: "Mock GPT-4 response: [mock output for testing]"
        # Override by keyword
        patterns:
          debug: "Mock response: Debugged your code successfully"
          refactor: "Mock response: Code refactored for clarity"

      claude-3-sonnet:
        default: "Mock Claude response: [test output]"
        patterns:
          explain: "Mock response: Explained the concept clearly"

  # Real providers: configured but not used in dev
  openai:
    type: openai
    api_key: ${OPENAI_API_KEY}
    enabled: false  # Disabled in dev

  anthropic:
    type: anthropic
    api_key: ${ANTHROPIC_API_KEY}
    enabled: false

models:
  # Development: use mock
  gpt-4:
    provider: mock
    fallback_provider: mock  # Never fall back to real API in dev

  gpt-3.5-turbo:
    provider: mock
    fallback_provider: mock

  claude-3-sonnet:
    provider: mock
    fallback_provider: mock

  # Real providers commented out for dev
  # gpt-4-prod: { provider: openai }
  # claude-3-sonnet-prod: { provider: anthropic }
```

**Production config (config.prod.yaml):**
```yaml
providers:
  openai:
    type: openai
    api_key: ${OPENAI_API_KEY}
    enabled: true

  anthropic:
    type: anthropic
    api_key: ${ANTHROPIC_API_KEY}
    enabled: true

  # Mock disabled in production
  mock:
    type: mock
    enabled: false

models:
  gpt-4: { provider: openai, fallback_provider: anthropic }
  gpt-3.5-turbo: { provider: openai, fallback_provider: anthropic }
  claude-3-sonnet: { provider: anthropic, fallback_provider: openai }
```

## Test & Verify

**Step 1:** Validate dev config:
```bash
tokenpak validate-config config.yaml
# Expected output:
# ✓ Config valid
# ✓ Providers: mock (enabled)
# ✓ Real providers: openai (disabled), anthropic (disabled)
# ✓ All models route to mock
```

**Step 2:** Start proxy in dev mode:
```bash
tokenpak proxy --config config.yaml
# Should start instantly (no API key validation)
```

**Step 3:** Make a request (mock response, instant):
```bash
time curl -X POST http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Debug my code"}]
  }' -s | jq '.content'

# Expected output:
# "Mock response: Debugged your code successfully"
# real 0.2s (includes mock latency, very fast)
```

**Step 4:** Make many requests without API costs:
```bash
# Simulate high-volume testing
for i in {1..100}; do
  curl -X POST http://localhost:8000/v1/messages \
    -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "request $i"}]}' \
    -s > /dev/null
done
echo "Made 100 requests, $0 cost!"
```

**Step 5:** Verify no real API calls (check logs):
```bash
tokenpak logs --provider openai
# Expected output: EMPTY (no real API calls)

tokenpak logs --provider mock
# Expected output: 100 calls to mock provider
```

**Step 6:** Switch to production config:
```bash
# Stop dev proxy
pkill -f "tokenpak proxy --config config.yaml"

# Start production proxy
tokenpak proxy --config config.prod.yaml
# Now real API calls will be made
```

**Step 7:** Verify switch worked:
```bash
curl -X POST http://localhost:8000/v1/messages \
  -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "Real request"}]}' \
  -s | jq '.cost_cents'

# Expected output: non-zero cost (real API call)
```

## Integration Example (Python)

```python
# app.py - Same code works in dev or prod
import requests
import os

def get_ai_response(prompt):
    response = requests.post(
        'http://localhost:8000/v1/messages',
        json={
            'model': 'gpt-4',  # Uses mock in dev, real in prod
            'messages': [{'role': 'user', 'content': prompt}]
        }
    )
    return response.json()['content']

# Run in dev: fast, free, deterministic
# Run in prod: slow, costs money, real answers

if __name__ == '__main__':
    mode = os.getenv('ENV', 'dev')
    print(f"Running in {mode} mode")
    print(get_ai_response("Hello"))
```

**Run in dev:**
```bash
ENV=dev python app.py
# Output: Mock response: [test output]
# No API calls, instant
```

**Run in prod:**
```bash
ENV=prod python app.py
# Output: Real GPT-4 response
# Costs money, realistic
```

## What Just Happened

TokenPak routed your request to the mock provider in development:

1. **Request arrives** with model `gpt-4`
2. **Provider lookup** finds `gpt-4 → mock`
3. **Mock provider** returns canned response instantly
4. **Client receives** response without any real API call

In production, the same code routes to real providers — no application changes needed.

## Common Pitfalls

**Pitfall 1: Mock responses are too different from reality**
- ❌ Wrong: Mock always says "success", real API has variability
- ✅ Right: Make mocks realistic: include error cases, vary response lengths

**Pitfall 2: Forgetting to switch configs**
- ❌ Wrong: Deploy to production with `config.yaml` (dev mocks enabled)
- ✅ Right: CI/CD enforces `config.prod.yaml` on production deployments

**Pitfall 3: Mock latency is too low**
- ❌ Wrong: `latency_ms: 0` (tests pass in dev, timeout in prod)
- ✅ Right: `latency_ms: 200` - 500 (realistic, catches slow code paths)

**Pitfall 4: Real providers still enabled in dev**
- ❌ Wrong: `enabled: true` for OpenAI in dev, can accidentally burn budget
- ✅ Right: Explicitly `enabled: false` for real providers in dev config

**Pitfall 5: Mock responses are too static**
- ❌ Wrong: Every request returns identical response
- ✅ Right: Vary by prompt keyword: `patterns: {debug: "...", refactor: "..."}`

**Pitfall 6: No way to test fallback logic**
- ❌ Wrong: Can't test "what if primary provider fails?" in dev
- ✅ Right: Add mock provider with `failure_rate: 0.2` to test fallbacks
