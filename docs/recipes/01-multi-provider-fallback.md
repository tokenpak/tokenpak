# Recipe: Multi-Provider Fallback

**What this solves:** Route requests to Anthropic if your primary provider (OpenAI) experiences an outage or rate limit.

## Prerequisites
- TokenPak installed: `pip install tokenpak`
- Valid API keys for both Anthropic and OpenAI
- `tokenpak` CLI available in your shell

## Config Snippet

```yaml
# config.yaml
providers:
  openai:
    type: openai
    api_key: ${OPENAI_API_KEY}
    models:
      gpt-4: {}
      gpt-3.5-turbo: {}

  anthropic:
    type: anthropic
    api_key: ${ANTHROPIC_API_KEY}
    models:
      claude-3-sonnet: {}
      claude-3-opus: {}

models:
  gpt-4:
    provider: openai
    # Fallback chain: try OpenAI first, then Anthropic
    fallback_to: claude-3-sonnet

  gpt-3.5-turbo:
    provider: openai
    fallback_to: claude-3-sonnet

  claude-3-sonnet:
    provider: anthropic

  claude-3-opus:
    provider: anthropic
```

## Test & Verify

**Step 1:** Save the config above to `config.yaml` and validate it:
```bash
tokenpak validate-config config.yaml
# Expected output:
# ✓ Config valid
# ✓ Providers: openai, anthropic
# ✓ Models: gpt-4, gpt-3.5-turbo, claude-3-sonnet, claude-3-opus
# ✓ Fallback chains verified
```

**Step 2:** Simulate OpenAI being down (set a fake key):
```bash
export OPENAI_API_KEY="sk-fake-key-for-testing"
export ANTHROPIC_API_KEY="sk-ant-real-key"

# Try to use OpenAI — should fail over to Anthropic
tokenpak proxy --config config.yaml
# In another terminal:
curl -X POST http://localhost:8000/v1/messages \
  -H "Authorization: Bearer test" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Say OK"}]
  }' 2>&1 | grep -q '"content"'
```

**Expected output:**
```
Request to OpenAI gpt-4 failed (connection refused).
Falling back to Anthropic claude-3-sonnet.
✓ Anthropic responded successfully.
```

## What Just Happened

TokenPak evaluated the request against `gpt-4` (mapped to OpenAI). The provider was unavailable, so TokenPak:

1. Checked the `fallback_to` chain in the config
2. Found `claude-3-sonnet` in the fallback
3. Routed the request to Anthropic instead
4. Returned the response to the client

The client received a valid response without needing to know about the failover — it's transparent to your application.

## Common Pitfalls

**Pitfall 1: Fallback chain is too short**
- ❌ Wrong: Single provider with no fallback
- ✅ Right: Chain at least 2 providers: `openai → anthropic → vertex-ai`

**Pitfall 2: Fallback loops**
- ❌ Wrong: `gpt-4 → gpt-3.5-turbo → gpt-4` (circular)
- ✅ Right: Always point to a different provider down the chain

**Pitfall 3: API keys for fallback not configured**
- ❌ Wrong: Forget to set `ANTHROPIC_API_KEY` env var, fallback fails
- ✅ Right: Pre-check all keys in the chain before deploying: `tokenpak list-keys`

**Pitfall 4: Rate limits on fallback**
- ❌ Wrong: Fallback provider has lower rate limits than primary
- ✅ Right: Verify fallback provider limits are sufficient: `tokenpak show-limits --provider anthropic`
