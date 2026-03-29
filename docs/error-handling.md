---
title: "error-handling"
created: 2026-03-24T19:05:55Z
---
# Error Handling & Troubleshooting

TokenPak provides normalized error handling across all providers, automatic retries, and fallback chains.

---

## Common Errors & Solutions

### 1. Connection Refused (Proxy Not Running)

**Error Message:**
```
ConnectionRefusedError: [Errno 111] Connection refused
Failed to connect to http://127.0.0.1:8000
```

**Cause:** The TokenPak proxy server is not running.

**Solution:**
```bash
# Start the proxy
tokenpak serve

# (in another terminal)
python your_script.py
```

**Prevention:** Keep the proxy running in a background process or systemd service.

---

### 2. Authentication Failed (Invalid API Key)

**Error Message:**
```
AuthenticationError: Invalid API key for provider: anthropic
Check your ANTHROPIC_API_KEY environment variable
```

**Cause:** Missing or incorrect API key.

**Solution:**
```bash
# Check if key is set
echo $ANTHROPIC_API_KEY

# Set the key
export ANTHROPIC_API_KEY="sk-ant-..."

# Restart the proxy
tokenpak serve
```

**Prevention:**
- Use a `.env` file (see [Installation](./installation.md))
- Check key format (should start with `sk-ant-`, `sk-`, or `AIza-`)
- Rotate expired keys immediately

---

### 3. Rate Limit Exceeded

**Error Message:**
```
RateLimitError: Rate limit exceeded (429)
Retry-After: 60
```

**Cause:** Too many requests to the provider in a short time.

**Solution (Automatic):**
TokenPak automatically retries with exponential backoff:
```
Attempt 1: Wait 1 second, retry
Attempt 2: Wait 2 seconds, retry
Attempt 3: Wait 4 seconds, retry
Attempt 4: Wait 8 seconds, retry
(Circuit breaker opens, switch to fallback provider)
```

**Solution (Manual):**
```python
from tokenpak import Client, RateLimitError
import time

client = Client(api_key="...", model="claude-opus-4-6")

try:
    response = client.messages.create(...)
except RateLimitError as e:
    wait_seconds = int(e.headers.get("Retry-After", 60))
    print(f"Rate limited. Waiting {wait_seconds}s...")
    time.sleep(wait_seconds)
    # Retry
    response = client.messages.create(...)
```

**Prevention:**
- Implement request batching (fewer, larger requests)
- Use fallback chains for load balancing
- Monitor your request frequency

---

### 4. Model Not Found

**Error Message:**
```
ModelNotFoundError: Model 'gpt-7' not found in provider: openai
Available models: gpt-4o, gpt-4-turbo, gpt-3.5-turbo
```

**Cause:** Using a model name that the provider doesn't support.

**Solution:**
```python
# Check available models for your provider
client = Client(api_key="...", model="gpt-4o")

# Use a valid model name
response = client.messages.create(
    model="gpt-4o",  # Valid
    messages=[...]
)
```

**Common Model Names:**

| Provider | Models |
|----------|--------|
| Anthropic | `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-3-5` |
| OpenAI | `gpt-4o`, `gpt-4-turbo`, `gpt-3.5-turbo` |
| Google | `gemini-pro`, `gemini-pro-vision`, `gemini-ultra` |

**Prevention:** Hardcode model names; don't accept user input directly.

---

### 5. Provider Timeout

**Error Message:**
```
TimeoutError: Request to anthropic timed out after 300 seconds
```

**Cause:** Provider took too long to respond.

**Solution (TokenPak Automatic):**
Switches to fallback provider if primary times out.

**Solution (Manual):**
```python
client = Client(
    api_key="...",
    model="claude-opus-4-6",
    timeout=60  # 60 second timeout
)

try:
    response = client.messages.create(...)
except TimeoutError:
    # Try fallback manually
    client.model = "gemini-pro"
    response = client.messages.create(...)
```

**Prevention:**
- Use fallback chains
- Set reasonable timeouts
- Monitor provider status

---

### 6. Token Limit Exceeded

**Error Message:**
```
TokenLimitError: Message exceeds max_tokens limit (4096 > 4096)
```

**Cause:** Request too large for the model.

**Solution:**
```python
# Option 1: Reduce message size
short_context = "Summary of relevant context only..."

# Option 2: Use compression (automatic)
client = Client(
    api_key="...",
    model="claude-opus-4-6",
    compression=True  # Auto-compress context
)

# Option 3: Split into multiple requests
# (batch processing)
```

**Prevention:**
- Use `count_tokens()` before making requests
- Implement compression (automatic in FREE)
- Use document injection selectively

---

### 7. Invalid Configuration

**Error Message:**
```
ConfigError: Invalid config.yaml syntax at line 5:
  compression.enabled must be a boolean, got 'yes'
```

**Cause:** Malformed YAML or invalid option.

**Solution:**
```yaml
# Wrong
compression:
  enabled: yes  # ❌ Should be true/false

# Right
compression:
  enabled: true  # ✅
```

**Validation:**
```bash
# Validate config before starting
tokenpak validate --config config.yaml

# Shows all errors
```

**Prevention:**
- Use YAML validator: https://yamllint.com/
- Check indentation (spaces, not tabs)
- Refer to [Installation guide](./installation.md) for examples

---

## Fallback Chains & Circuit Breaker

TokenPak automatically switches providers when the primary fails.

### How It Works

```yaml
provider: anthropic
fallback:
  - google      # Try if Anthropic fails
  - openai      # Try if Google fails
```

**Request flow:**
```
1. Try Anthropic
   ├─ Success? ✅ Return response
   ├─ Timeout? → Try Google
   ├─ Rate limit? → Wait then retry
   └─ Permanent error? → Try Google

2. Try Google
   ├─ Success? ✅ Return response
   └─ Fail? → Try OpenAI

3. Try OpenAI
   ├─ Success? ✅ Return response
   └─ Fail? → Return error to client
```

### Circuit Breaker

When a provider fails repeatedly, TokenPak opens the **circuit breaker** to prevent cascading failures:

```
State: CLOSED (normal operation)
  └─ 3 failures in 60 seconds → OPEN

State: OPEN (provider is down)
  └─ Skip to fallback provider
  └─ After 300 seconds → HALF_OPEN

State: HALF_OPEN (testing recovery)
  └─ Try 1 request
  ├─ Success? → CLOSED
  └─ Fail? → OPEN (restart 300s timer)
```

### Configuration

```yaml
fallback:
  - anthropic
  - google
  - openai

circuit_breaker:
  failure_threshold: 3      # Open after 3 failures
  recovery_timeout: 300     # Reset after 5 minutes
  half_open_requests: 1     # Test 1 request in half-open
```

---

## Monitoring & Debugging

### Enable Debug Logging

```bash
# Detailed logs
TOKENPAK_LOG_LEVEL=DEBUG tokenpak serve

# Write to file
tokenpak serve --log-file /tmp/tokenpak.log
```

### Check Proxy Status

```bash
# Health check endpoint
curl http://127.0.0.1:8000/health

# Response:
# {
#   "status": "healthy",
#   "providers": {
#     "anthropic": "ok",
#     "google": "ok",
#     "openai": "degraded"
#   }
# }
```

### View Request Log

```bash
# Last 100 requests
curl http://127.0.0.1:8000/logs?limit=100

# Requests to Anthropic only
curl 'http://127.0.0.1:8000/logs?provider=anthropic'

# Requests with errors
curl 'http://127.0.0.1:8000/logs?status=error'
```

### Test Provider Connectivity

```python
from tokenpak import Client

client = Client(api_key="...", model="claude-opus-4-6")

# Quick test
try:
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=10,
        messages=[{"role": "user", "content": "test"}]
    )
    print("✅ Connected to Anthropic")
except Exception as e:
    print(f"❌ Connection failed: {e}")
```

---

## Error Types Reference

### Client Errors (4xx)

| Error | Code | Cause | Solution |
|-------|------|-------|----------|
| `AuthenticationError` | 401 | Invalid API key | Check API key in `.env` |
| `PermissionError` | 403 | Key lacks permissions | Regenerate API key |
| `NotFoundError` | 404 | Model not found | Check model name |
| `RateLimitError` | 429 | Too many requests | Use fallback chain |
| `TokenLimitError` | 413 | Message too large | Compress or split |
| `ValidationError` | 400 | Invalid request format | Check request structure |

### Server Errors (5xx)

| Error | Code | Cause | Solution |
|-------|------|-------|----------|
| `ServerError` | 500 | Provider internal error | Retry with fallback |
| `ServiceUnavailableError` | 503 | Provider down | Use fallback chain |
| `GatewayError` | 502 | Network issue | Check connection |
| `TimeoutError` | 504 | Request took too long | Increase timeout |

### Network Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `ConnectionRefusedError` | Proxy not running | Start `tokenpak serve` |
| `ConnectionError` | Network unreachable | Check internet connection |
| `SSLError` | Certificate validation failed | Check CA certificates |

---

## Best Practices

### 1. Always Use Fallback Chains

```yaml
provider: anthropic
fallback:
  - google
  - openai
```

### 2. Wrap Requests in Try-Catch

```python
try:
    response = client.messages.create(...)
except RateLimitError:
    # Handle rate limit
    pass
except AuthenticationError:
    # Handle auth error
    pass
except Exception as e:
    # Log unexpected errors
    logger.error(f"Unexpected: {e}")
```

### 3. Implement Exponential Backoff

TokenPak does this automatically, but for custom retries:

```python
import time

def call_with_backoff(fn, max_attempts=3):
    for attempt in range(max_attempts):
        try:
            return fn()
        except RateLimitError:
            wait = 2 ** attempt  # 1, 2, 4 seconds
            print(f"Attempt {attempt + 1} failed. Waiting {wait}s...")
            time.sleep(wait)
    raise Exception("All attempts failed")
```

### 4. Monitor Token Usage

```python
# Before making request
tokens = client.count_tokens(
    model="claude-opus-4-6",
    messages=messages
)

if tokens > 10000:
    print(f"Warning: {tokens} tokens. Consider compression.")
```

### 5. Set Timeouts

```python
client = Client(
    api_key="...",
    timeout=30,  # 30 second timeout
    model="claude-opus-4-6"
)
```

---

## Getting Help

- **Question?** Check this guide or [FAQ](FAQ.md)
- **Bug?** Open an issue on GitHub
- **Stuck?** Email support or check Discord

---

## Next Steps

- **Monitoring:** See [Observability Guide](./observability.md)
- **Performance:** Check [Feature Matrix](./features.md) for optimization tips
- **Adapters:** See [Adapter Reference](./adapters.md) for provider-specific notes
