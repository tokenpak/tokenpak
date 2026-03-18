# TokenPak Usage Examples

This directory contains copy-paste-ready recipes for common TokenPak usage patterns. Each example is self-contained and can be run independently.

## Quick Navigation

| Example | Purpose | When to Use |
|---------|---------|------------|
| **basic_python_usage.py** | Basic proxy setup | Getting started with TokenPak |
| **http_request_json.py** | Raw HTTP requests | Testing proxy endpoints directly |
| **caching_pattern.py** | Cache hit/miss tracking | Understanding cache behavior |
| **cost_tracking.py** | Token savings tracking | Monitoring usage and costs |
| **openai_sdk_integration.py** | OpenAI SDK drop-in | Using OpenAI library with TokenPak |
| **langchain_integration.py** | LangChain support | Building agents and chains |
| **budget_enforcement.py** | Monthly budget limits | Preventing overspend |
| **fallback_chain.py** | Multi-provider failover | Resilience and cost optimization |
| **docker_python_client.py** | Containerized deployment | Production setup |
| **batch_async_processing.py** | Concurrent requests | Speeding up batch operations |

## Running Examples

### Prerequisites

1. **Start the TokenPak ingest API server:**
   ```bash
   # In one terminal:
   cd ~/vault/01_PROJECTS/ocp-protocol/packages/pypi
   python -m tokenpak.agent.ingest.api
   ```
   
   The server will be available at `http://localhost:8766` by default.

2. **Set environment variables:**
   ```bash
   export ANTHROPIC_API_KEY=sk-...  # Your actual key
   export TOKENPAK_PROXY_URL=http://localhost:8766
   ```

### Running an Example

```bash
cd ~/vault/01_PROJECTS/ocp-protocol/packages/pypi/examples

# Basic Python usage
python basic_python_usage.py

# Caching pattern
python caching_pattern.py

# Batch processing
python batch_async_processing.py
```

## Example Structure

Each example follows this pattern:

```python
"""
Example title and brief description.

What this example shows:
- Key feature 1
- Key feature 2
- Key feature 3

When to use this:
- Use case 1
- Use case 2
"""

# Setup (imports, config)
# Implementation (working code)
# Example output (printed results)
```

## Key Concepts

### Health Check
TokenPak exposes a `/health` endpoint to verify the proxy is running:
```python
import requests
response = requests.get("http://localhost:8766/health")
assert response.status_code == 200
```

### Token Counting
Each request returns token counts:
```json
{
  "usage": {
    "prompt_tokens": 150,
    "completion_tokens": 45,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0
  }
}
```

### Cache Hits
TokenPak can detect when context is reused (cache hits):
```python
# First request: cache miss (cache_read_input_tokens = 0)
response1 = proxy.chat.completions.create(...)

# Second request with same context: cache hit
response2 = proxy.chat.completions.create(...)
# response2.usage.cache_read_input_tokens > 0
```

### Cost Tracking
Token usage maps to cost:
```python
# Input tokens: $0.003 per 1K
# Output tokens: $0.015 per 1K
# Cache reads: $0.0003 per 1K (90% savings!)

prompt_cost = (prompt_tokens / 1000) * 0.003
completion_cost = (completion_tokens / 1000) * 0.015
cache_cost = (cache_tokens / 1000) * 0.0003
total = prompt_cost + completion_cost + cache_cost
```

## Common Patterns

### Pattern 1: Simple Chat

```python
from tokenpak import TokenPakProxy

proxy = TokenPakProxy(api_key="sk-...", proxy_url="http://localhost:8766")

response = proxy.chat.completions.create(
    model="claude-sonnet-4-6",
    messages=[
        {"role": "user", "content": "What is 2+2?"}
    ]
)

print(response.choices[0].message.content)
print(f"Tokens: {response.usage.prompt_tokens} + {response.usage.completion_tokens}")
```

### Pattern 2: Streaming

```python
stream = proxy.chat.completions.create(
    model="claude-sonnet-4-6",
    messages=[{"role": "user", "content": "Write a poem"}],
    stream=True
)

for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

### Pattern 3: With Tools/Functions

```python
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"}
                },
                "required": ["location"]
            }
        }
    }
]

response = proxy.chat.completions.create(
    model="claude-sonnet-4-6",
    messages=[{"role": "user", "content": "What's the weather in NYC?"}],
    tools=tools
)
```

## Troubleshooting

### Connection Refused
```
error: connect ECONNREFUSED 127.0.0.1:8766
```
→ Start the TokenPak server first: `python -m tokenpak.agent.ingest.api`

### Invalid API Key
```
error: Invalid API key provided
```
→ Check that `ANTHROPIC_API_KEY` env var is set correctly

### Module Not Found
```
error: No module named 'tokenpak'
```
→ Install TokenPak: `pip install tokenpak` (or `pip install -e .` in the repo)

### Timeout
```
error: Request timed out after 30s
```
→ Increase timeout: `proxy.chat.completions.create(..., timeout=60)`

## Modifying Examples

All examples can be customized:

- **Change the model:** Use any Anthropic model (`claude-sonnet-4-6`, `claude-haiku-4-5`, etc.)
- **Change the prompt:** Replace the user message with your own question
- **Change the proxy URL:** Set `TOKENPAK_PROXY_URL` env var or pass `proxy_url=` parameter
- **Disable caching:** Set `cache_control={"type": "ephemeral"}` in system prompt (advanced)

## Performance Tips

1. **Reuse the proxy client:** Don't create a new client for every request
2. **Enable streaming:** For long responses, stream chunks as they arrive
3. **Batch requests:** Use async batch processing for multiple requests
4. **Monitor cache:** Check cache hit rate to identify reusable context
5. **Budget wisely:** Set monthly limits to avoid unexpected costs

## Next Steps

- **Production deployment:** See `docker_python_client.py` for containerized setup
- **Advanced features:** Check the TokenPak documentation for caching strategies, budget enforcement, and fallback chains
- **Monitoring:** Use the dashboard to track usage over time

---

**Questions?** Check the main TokenPak documentation or open an issue on GitHub.
