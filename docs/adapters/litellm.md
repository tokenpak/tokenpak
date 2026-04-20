# TokenPak + LiteLLM Adapter

[LiteLLM](https://github.com/BerriAI/litellm) is a multi-provider LLM router that abstracts away provider differences under a unified OpenAI-compatible API. By routing LiteLLM through TokenPak, you gain automatic compression, caching, and token accounting — all while maintaining LiteLLM's multi-provider fallback and cost-optimization features.

## Why Use LiteLLM + TokenPak?

| Feature | LiteLLM | TokenPak | Together |
|---------|---------|----------|----------|
| **Multi-provider routing** | ✅ Fallback, cost optimization | — | ✅ Add compression + caching |
| **OpenAI compatibility** | ✅ Unified API | ✅ `/v1/chat/completions` | ✅ Seamless integration |
| **Token compression** | — | ✅ Reduce input/output tokens | ✅ Lower costs further |
| **Request caching** | — | ✅ Cache identical prompts | ✅ Deduplicate across clients |
| **Token accounting** | Limited | ✅ Detailed stats/usage | ✅ Unified usage tracking |

### Use Cases
- **Multi-provider fallback** with TokenPak compression: Use LiteLLM's fallback to Claude → Gemini → GPT, with TokenPak deduplicating requests across all routes
- **Cost optimization** across providers: LiteLLM optimizes provider selection, TokenPak optimizes tokens — compound savings
- **Controlled multi-client access**: Route multiple services through TokenPak proxy + LiteLLM for unified auth and cost tracking

---

## Setup

### 1. Install TokenPak and LiteLLM

```bash
pip install litellm

# TokenPak runs as a standalone proxy service
# See: https://github.com/...tokenpak#getting-started
```

### 2. Start TokenPak Proxy

```bash
# TokenPak default: http://localhost:8766/v1
python -m tokenpak.proxy --port 8766
```

Verify proxy is running:
```bash
curl http://localhost:8766/health
# { "status": "ok" }
```

---

## Usage Patterns

### Pattern 1: Direct `litellm.completion()`

Route a single completion request through TokenPak:

```python
import litellm

# Set your Anthropic API key
import os
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-..."

response = litellm.completion(
    model="openai/claude-sonnet-4-6",  # "openai/" prefix = OpenAI-compatible endpoint
    api_base="http://localhost:8766/v1",  # Point to TokenPak proxy
    messages=[
        {"role": "user", "content": "What is quantum computing?"}
    ],
    temperature=0.7
)

print(response.choices[0].message.content)
```

**Key points:**
- Use `openai/<model-name>` format: LiteLLM routes to the OpenAI-compatible endpoint
- `api_base` points to TokenPak proxy (default: `localhost:8766/v1`)
- TokenPak handles compression, caching, and token accounting

---

### Pattern 2: LiteLLM Proxy Mode with TokenPak Backend

Run LiteLLM's own proxy to manage multiple clients, all routing through TokenPak:

```yaml
# litellm_config.yaml
model_list:
  - model_name: "claude-sonnet"
    litellm_params:
      model: "openai/claude-sonnet-4-6"
      api_base: "http://localhost:8766/v1"
      api_key: "sk-ant-..."  # Or set via env var: ANTHROPIC_API_KEY

  - model_name: "claude-opus"
    litellm_params:
      model: "openai/claude-opus-4-6"
      api_base: "http://localhost:8766/v1"
      api_key: "sk-ant-..."

router_settings:
  fallback_ratio: 0.1  # Fallback after 10% failure rate
```

**Start LiteLLM proxy:**
```bash
litellm --config litellm_config.yaml --port 8000
```

**Use from your application:**
```python
import litellm

response = litellm.completion(
    model="claude-sonnet",  # Routes to LiteLLM proxy
    api_base="http://localhost:8000",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

**Flow:**
```
Your App → LiteLLM Proxy (8000) → TokenPak Proxy (8766) → Anthropic API
```

---

### Pattern 3: Multi-Provider Fallback with TokenPak Caching

Combine LiteLLM's fallback logic with TokenPak's caching for resilient + efficient routing:

```yaml
# litellm_config.yaml
model_list:
  - model_name: "smart-router"
    litellm_params:
      model: "openai/claude-opus-4-6"
      api_base: "http://localhost:8766/v1"  # Primary: TokenPak → Claude
      api_key: "sk-ant-..."

  - model_name: "smart-router"
    litellm_params:
      model: "openai/gpt-4-turbo"
      api_base: "http://localhost:8766/v1"  # Fallback: TokenPak → GPT-4
      api_key: "sk-openai-..."

router_settings:
  fallback_ratio: 0.2  # Fallback after 20% failure rate
  allowed_fails: 1  # Allow 1 request to fail before fallback kicks in
```

**How it works:**
1. LiteLLM routes 80% of requests to Claude (primary)
2. On failures, automatically routes to GPT-4
3. **TokenPak caches both paths** — identical prompts are deduplicated across providers
4. Unified token usage tracking across all routes

---

## Model Name Convention

TokenPak uses the `openai/<model-name>` pattern for all providers. This tells LiteLLM to treat the endpoint as OpenAI-compatible.

| Provider | Model Name |
|----------|-----------|
| **Anthropic** | `openai/claude-opus-4-6`, `openai/claude-sonnet-4-6` |
| **OpenAI** | `openai/gpt-4-turbo`, `openai/gpt-4o` |
| **Google** | `openai/gemini-2.0-flash` |

See TokenPak Model Support for the full list.

---

## Verify Routing

Check that requests are flowing through TokenPak correctly:

```bash
# Check TokenPak stats endpoint
curl http://localhost:8766/stats | jq .

# Example output:
{
  "cached": 12,
  "compressed_requests": 45,
  "token_usage": {
    "input": 2840,
    "output": 1230,
    "cached": 340  # Tokens saved by caching
  }
}
```

If your request doesn't appear in stats, check:
1. TokenPak is running (`curl http://localhost:8766/health`)
2. `api_base` in LiteLLM config matches TokenPak port (default `8766`)
3. Firewall/network allows `localhost:8766` connection

---

## Limitations & Known Gaps

### LiteLLM Specific

| Limitation | Impact | Workaround |
|-----------|--------|-----------|
| **Streaming** | LiteLLM streaming via TokenPak works, but caching doesn't apply to streamed responses | Cache only unstreamed requests |
| **Custom headers** | LiteLLM passes headers through; TokenPak ignores non-standard headers | Use `api_key` + `api_base` only |
| **Async routing** | LiteLLM's async API works with TokenPak, but fallback logic runs serially | Acceptable for most use cases |

### TokenPak Specific

| Limitation | Impact | Workaround |
|-----------|--------|-----------|
| **Cache key** | Caching uses model + prompt hash (ignores temperature, top_p) | Ideal for deterministic requests; less useful for creative tasks |
| **Rate limits** | TokenPak respects upstream rate limits; LiteLLM's retry logic stacks on top | Configure reasonable `max_retries` in LiteLLM config |
| **Request size** | Max request size is 10 MB | Unlikely to hit in practice |

---

## Troubleshooting

### "Connection refused" on `api_base`

**Error:**
```
litellm.exceptions.APIConnectionError: Failed to connect to http://localhost:8766/v1
```

**Fix:**
1. Verify TokenPak is running: `curl http://localhost:8766/health`
2. Check port is correct (default `8766`)
3. If remote: use actual IP instead of `localhost` (e.g., `http://192.0.2.100:8766/v1` — substitute your proxy host's real IP)

---

### "Invalid API key" errors

**Error:**
```
litellm.exceptions.AuthenticationError: Invalid API key
```

**Fix:**
1. Verify `ANTHROPIC_API_KEY` is set correctly
2. TokenPak passes your API key upstream — check it matches your provider (Anthropic, OpenAI, etc.)
3. Test directly against TokenPak: `curl -H "Authorization: Bearer sk-ant-..." http://localhost:8766/v1/models`

---

### Requests not cached

**Symptom:** Stats show `cached: 0` even after repeated identical requests

**Cause:** Likely mismatch in request parameters (temperature, top_p, etc.)

**Fix:**
1. Cache key = model + prompt hash. Other parameters are ignored.
2. Check your requests are truly identical:
   ```python
   # These will cache:
   msg = [{"role": "user", "content": "What is 2+2?"}]
   r1 = litellm.completion(model="openai/claude-sonnet-4-6", messages=msg, api_base=proxy)
   r2 = litellm.completion(model="openai/claude-sonnet-4-6", messages=msg, api_base=proxy)
   
   # These won't cache (different parameters):
   r1 = litellm.completion(model="openai/claude-sonnet-4-6", messages=msg, temperature=0.7, api_base=proxy)
   r2 = litellm.completion(model="openai/claude-sonnet-4-6", messages=msg, temperature=0.5, api_base=proxy)
   ```

---

## Next Steps

- OpenAI SDK Adapter — Use TokenPak with `openai` library
- [LangChain Adapter](./langchain.md) — Integrate with LangChain
- [TokenPak Configuration](../configuration.md) — Advanced proxy settings
- Model Support — Full provider & model reference
