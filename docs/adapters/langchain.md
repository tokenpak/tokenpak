# LangChain Adapter — Quick-Start

Route your LangChain applications through the TokenPak proxy for cost tracking, compression, and vault context injection.

## Why Route Through TokenPak?

- **Cost tracking:** Every token counted and categorized by model/provider
- **Request compression:** Reduces token usage via built-in context optimization
- **Vault injection:** Automatically enrich prompts with your knowledge base
- **Request caching:** Deduplicate identical calls across applications
- **Usage analytics:** Dashboard metrics for model spend and performance

## Prerequisites

1. **TokenPak proxy running locally**
   ```bash
   python3 ~/vault/01_PROJECTS/tokenpak/proxy.py &
   # Proxy starts on http://localhost:8766 by default
   ```

2. **LangChain and anthropic adapter installed**
   ```bash
   pip install langchain langchain-anthropic anthropic
   ```

3. **ANTHROPIC_API_KEY set**
   ```bash
   export ANTHROPIC_API_KEY="sk-ant-..."
   ```

## Quick Start (10 lines)

```python
from langchain_anthropic import ChatAnthropic

# Point ChatAnthropic at the TokenPak proxy instead of Anthropic directly
llm = ChatAnthropic(
    model="claude-3-5-sonnet-20241022",
    base_url="http://localhost:8766/v1",  # TokenPak proxy endpoint
    api_key="sk-ant-..."  # Proxy forwards this to Anthropic
)

# Use it normally — proxy handles everything behind the scenes
response = llm.invoke("What is Python good for?")
print(response.content)
```

**That's it.** All traffic flows through the proxy automatically.

## Configuration

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com/v1` | Overrides LLM endpoint (set to proxy) |
| `ANTHROPIC_API_KEY` | _none_ | API key (proxy forwards this) |
| `TOKENPAK_PROXY_URL` | `http://localhost:8766` | Proxy address if not default |
| `TOKENPAK_VAULT_PATH` | _none_ | Path to vault blocks for injection |

### Passing Config Directly

```python
from langchain_anthropic import ChatAnthropic
import os

llm = ChatAnthropic(
    model="claude-3-5-sonnet-20241022",
    base_url=os.getenv("TOKENPAK_PROXY_URL", "http://localhost:8766/v1"),
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    timeout=30,  # request timeout in seconds
)
```

## Verification — Check the Proxy

After making a few requests, verify they went through TokenPak:

```bash
# View proxy stats
curl http://localhost:8766/stats | python3 -m json.tool

# Expected output:
# {
#   "requests_processed": 5,
#   "total_input_tokens": 1250,
#   "total_output_tokens": 480,
#   "cache_hits": 1,
#   "models": {
#     "claude-3-5-sonnet-20241022": {
#       "calls": 5,
#       "input_tokens": 1250,
#       "output_tokens": 480
#     }
#   }
# }
```

If you see requests recorded, traffic is flowing correctly through the proxy.

## Common Errors & Fixes

### ❌ `Connection refused` / `Cannot connect to proxy`

**Cause:** Proxy not running or listening on wrong port.

**Fix:**
```bash
# Start proxy in background
python3 ~/vault/01_PROJECTS/tokenpak/proxy.py &

# Verify it's listening
curl http://localhost:8766/health
# Should return: {"status": "ok"}
```

---

### ❌ `Authentication failed` / `Invalid API key`

**Cause:** API key not set or proxy can't forward it.

**Fix:**
```bash
# Check key is exported
echo $ANTHROPIC_API_KEY  # Should print your key, not be empty

# Or pass explicitly
llm = ChatAnthropic(
    model="claude-3-5-sonnet-20241022",
    base_url="http://localhost:8766/v1",
    api_key="sk-ant-YOUR_KEY_HERE"  # Explicit > env var for debugging
)
```

---

### ❌ `404 Not Found` when calling proxy

**Cause:** Endpoint path missing `/v1` suffix or proxy address wrong.

**Fix:**
```python
# ✅ CORRECT
base_url="http://localhost:8766/v1"

# ❌ WRONG
base_url="http://localhost:8766"  # Missing /v1
```

---

### ❌ Requests not showing up in proxy stats

**Cause:** Traffic bypassing proxy or using different endpoint.

**Fix:**
```bash
# Check what URL LangChain is actually calling
import logging
logging.basicConfig(level=logging.DEBUG)
logging.getLogger("httpx").setLevel(logging.DEBUG)

# Now make a request — debug logs will show the URL being called
llm.invoke("test")

# Should see requests to http://localhost:8766/...
```

---

## Full Example Application

```python
"""
Complete example: chat app routing through TokenPak proxy
"""
import os
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

# Initialize with proxy endpoint
llm = ChatAnthropic(
    model="claude-3-5-sonnet-20241022",
    base_url=os.getenv("TOKENPAK_PROXY_URL", "http://localhost:8766/v1"),
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    timeout=60,
)

# Build conversation
messages = [
    SystemMessage(content="You are a helpful Python assistant."),
    HumanMessage(content="Write a function that checks if a number is prime."),
]

# Call through proxy
response = llm.invoke(messages)

print("Assistant:", response.content)

# Check proxy recorded it
import requests
stats = requests.get("http://localhost:8766/stats").json()
print(f"\nProxy stats: {stats['requests_processed']} requests processed")
```

## Advanced: Request Injection & Compression

TokenPak supports vault injection (prepend your knowledge base) and automatic compression. These work transparently with LangChain:

```python
import os
from langchain_anthropic import ChatAnthropic

llm = ChatAnthropic(
    model="claude-3-5-sonnet-20241022",
    base_url="http://localhost:8766/v1",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    # Optional: custom headers for injection/compression directives
    default_headers={
        "X-TokenPak-Vault": "~/vault/01_KNOWLEDGE",  # Auto-inject blocks
        "X-TokenPak-Compress": "true",  # Enable compression
    }
)

# All requests now use vault injection + compression
response = llm.invoke("What should I know about Python async?")
```

Check the main [TokenPak docs](../index.md) for detailed vault injection and compression configuration.

## Troubleshooting

- **Proxy won't start:** Check port 8766 isn't in use (`lsof -i :8766`)
- **Can't import langchain_anthropic:** Run `pip install langchain-anthropic --upgrade`
- **Requests timing out:** Increase timeout in `ChatAnthropic(timeout=120)`
- **Proxy crashes with errors:** Check logs: `tail -f /tmp/tokenpak-proxy.log`

## Next Steps

- [TokenPak main documentation](../index.md)
- [Adapter architecture](../adapters.md)
- Vault injection guide (if docs exist)
- [Performance tuning](../performance.md) (if docs exist)
