# OpenAI SDK Compatibility Guide

TokenPak exposes an OpenAI-compatible endpoint at `/v1/chat/completions`. If you're migrating from the OpenAI API or using tools that target the OpenAI format (LangChain `ChatOpenAI`, LiteLLM, Vercel AI SDK, etc.), point them at the TokenPak proxy with no code changes.

---

## How It Works

```
Your app (openai SDK) → POST /v1/chat/completions → TokenPak proxy → Anthropic API
```

TokenPak translates the OpenAI Chat Completions format to Anthropic's Messages API, applies compression + caching, then translates the response back.

---

## Python — openai SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8766/v1",
    api_key="your-anthropic-api-key",  # forwarded to Anthropic
)

response = client.chat.completions.create(
    model="claude-sonnet-4-6",          # Anthropic model via TokenPak routing
    messages=[
        {"role": "user", "content": "What is TokenPak?"}
    ],
)
print(response.choices[0].message.content)
```

### Streaming

```python
with client.chat.completions.create(
    model="claude-sonnet-4-6",
    messages=[{"role": "user", "content": "Explain caching in one paragraph."}],
    stream=True,
) as stream:
    for chunk in stream:
        delta = chunk.choices[0].delta.content or ""
        print(delta, end="", flush=True)
```

---

## curl

```bash
curl http://localhost:8766/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-anthropic-api-key" \
  -d '{
    "model": "claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Hello from curl!"}]
  }'
```

### Streaming via curl

```bash
curl http://localhost:8766/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-anthropic-api-key" \
  -d '{
    "model": "claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Count to 5."}],
    "stream": true
  }' --no-buffer
```

---

## LangChain ChatOpenAI

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://localhost:8766/v1",
    api_key="your-anthropic-api-key",
    model="claude-sonnet-4-6",
)
result = llm.invoke("What is 2 + 2?")
print(result.content)
```

---

## Model Names

Pass Anthropic model IDs directly — TokenPak routes them to Anthropic:

| Model string | Routes to |
|---|---|
| `claude-sonnet-4-6` | Anthropic claude-sonnet-4-6 |
| `claude-haiku-4-5` | Anthropic claude-haiku-4-5 |
| `claude-opus-4-6` | Anthropic claude-opus-4-6 |

TokenPak aliases (e.g. `tokenpak-anthropic/claude-sonnet-4-6`) are also accepted.

---

## Supported Parameters

| Parameter | Supported | Notes |
|---|---|---|
| `model` | ✅ | Anthropic model IDs |
| `messages` | ✅ | Full role/content array |
| `stream` | ✅ | SSE streaming |
| `max_tokens` | ✅ | Passed through |
| `temperature` | ✅ | Passed through |
| `system` (top-level) | ✅ | Converted to Anthropic system prompt |
| `tools` / `tool_choice` | ✅ | Passed through |
| `n > 1` | ❌ | Only `n=1` supported |
| `logprobs` | ❌ | Not supported by Anthropic |

---

## Notes

- The proxy runs at `http://localhost:8766` by default. Change via `TOKENPAK_PORT`.
- Your API key is forwarded to Anthropic — use your real `ANTHROPIC_API_KEY`.
- Response format matches OpenAI Chat Completions shape (`.choices[0].message.content`).
- See [Getting Started](../getting-started.md) for proxy setup.
