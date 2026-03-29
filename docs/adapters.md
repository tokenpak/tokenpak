---
title: "adapters"
created: 2026-03-24T19:05:55Z
---
# Adapter Reference

Adapters are converters between your code's request/response format and each provider's native format. TokenPak includes **5 built-in adapters**, all FREE.

---

## Overview

| Adapter | Provider | Status | Best For |
|---------|----------|--------|----------|
| `anthropic` | Anthropic (Claude) | ✅ | Default, most mature |
| `openai_chat` | OpenAI Chat API | ✅ | GPT-4, GPT-3.5-Turbo |
| `openai_responses` | OpenAI Responses (Legacy) | ✅ | Older OpenAI integrations |
| `google` | Google Gemini | ✅ | Switching to Gemini |
| `passthrough` | Raw JSON | ✅ | Debugging, custom providers |

---

## 1. Anthropic Adapter

The **default adapter**. Uses the Anthropic (Claude) API format.

### Configuration

```yaml
# config.yaml
provider: anthropic
```

### Basic Usage

```python
from tokenpak import Client

client = Client(
    base_url="http://127.0.0.1:8000",
    api_key="sk-ant-...",
    model="claude-opus-4-6"
)

response = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=100,
    messages=[
        {"role": "user", "content": "What is 2 + 2?"}
    ]
)

print(response.content[0].text)  # "4"
```

### With System Prompt

```python
response = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=100,
    system="You are a helpful math tutor.",
    messages=[
        {"role": "user", "content": "Explain why 2 + 2 = 4"}
    ]
)
```

### With Tool Use

```python
response = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=100,
    tools=[
        {
            "name": "calculator",
            "description": "Perform a calculation",
            "input_schema": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Math expression"
                    }
                },
                "required": ["expression"]
            }
        }
    ],
    messages=[
        {"role": "user", "content": "What is 15 * 7?"}
    ]
)

# Handle tool use in response
if response.stop_reason == "tool_use":
    for block in response.content:
        if block.type == "tool_use":
            print(f"Tool: {block.name}, Input: {block.input}")
```

### Streaming

```python
stream = client.messages.stream(
    model="claude-opus-4-6",
    max_tokens=100,
    messages=[
        {"role": "user", "content": "Write a poem about tokenization"}
    ]
)

for text in stream.text_stream:
    print(text, end="", flush=True)
```

---

## 2. OpenAI Chat Adapter

Routes requests to OpenAI's Chat API (GPT-4, GPT-3.5-Turbo).

### Configuration

```yaml
provider: openai
model: gpt-4o
```

### Basic Usage

```python
from tokenpak import Client

client = Client(
    base_url="http://127.0.0.1:8000",
    api_key="sk-...",  # OpenAI key
    model="gpt-4o"
)

response = client.messages.create(
    model="gpt-4o",
    max_tokens=100,
    messages=[
        {"role": "user", "content": "Explain quantum computing briefly"}
    ]
)

print(response.content[0].text)
```

### Temperature & Top-P

```python
response = client.messages.create(
    model="gpt-4o",
    max_tokens=100,
    temperature=0.7,  # 0 = deterministic, 2 = creative
    top_p=0.9,  # nucleus sampling
    messages=[
        {"role": "user", "content": "Generate a creative story title"}
    ]
)
```

### Function Calling (OpenAI style)

```python
response = client.messages.create(
    model="gpt-4o",
    max_tokens=100,
    functions=[
        {
            "name": "get_weather",
            "description": "Get weather for a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "unit": {"type": "string", "enum": ["C", "F"]}
                },
                "required": ["location"]
            }
        }
    ],
    messages=[
        {"role": "user", "content": "What's the weather in San Francisco?"}
    ]
)
```

### JSON Mode

```python
response = client.messages.create(
    model="gpt-4o",
    max_tokens=200,
    response_format={"type": "json_object"},
    messages=[
        {
            "role": "user",
            "content": 'Return a JSON object with fields: "name", "age", "occupation"'
        }
    ]
)

# response.content[0].text is valid JSON
import json
data = json.loads(response.content[0].text)
```

### Streaming

```python
stream = client.messages.stream(
    model="gpt-4o",
    messages=[
        {"role": "user", "content": "Write a haiku"}
    ]
)

for chunk in stream:
    print(chunk.choices[0].delta.content, end="", flush=True)
```

---

## 3. OpenAI Responses Adapter (Legacy)

For older integrations using the OpenAI Responses API. **Not recommended for new projects** (OpenAI deprecated this).

### Configuration

```yaml
provider: openai_responses
model: text-davinci-003
```

### Basic Usage

```python
client = Client(
    base_url="http://127.0.0.1:8000",
    api_key="sk-...",
    model="text-davinci-003"
)

# Note: Different response format than Chat API
response = client.completions.create(
    model="text-davinci-003",
    prompt="Q: What is the capital of France?\nA:",
    max_tokens=10
)

print(response.choices[0].text)  # "Paris"
```

**Use OpenAI Chat adapter for new code.**

---

## 4. Google Gemini Adapter

Routes requests to Google's Gemini API.

### Configuration

```yaml
provider: google
model: gemini-pro
```

### Basic Usage

```python
from tokenpak import Client

client = Client(
    base_url="http://127.0.0.1:8000",
    api_key="AIza...",  # Google API key
    model="gemini-pro"
)

response = client.messages.create(
    model="gemini-pro",
    max_tokens=100,
    messages=[
        {"role": "user", "content": "What makes a good API design?"}
    ]
)

print(response.content[0].text)
```

### With System Instruction

```python
response = client.messages.create(
    model="gemini-pro",
    max_tokens=100,
    system="You are a world-class software architect.",
    messages=[
        {"role": "user", "content": "Design a payment system"}
    ]
)
```

### Function Calling (Google style)

```python
response = client.messages.create(
    model="gemini-pro",
    max_tokens=100,
    tools=[
        {
            "type": "function",
            "function": {
                "name": "search_web",
                "description": "Search the web",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"}
                    },
                    "required": ["query"]
                }
            }
        }
    ],
    messages=[
        {"role": "user", "content": "What is the latest version of Python?"}
    ]
)
```

### Vision/Multimodal

```python
import base64

# Read image
with open("image.jpg", "rb") as f:
    image_data = base64.standard_b64encode(f.read()).decode()

response = client.messages.create(
    model="gemini-pro-vision",
    max_tokens=200,
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_data
                    }
                }
            ]
        }
    ]
)
```

---

## 5. Passthrough Adapter

For debugging, custom providers, or testing. Sends raw JSON to the provider.

### Configuration

```yaml
provider: passthrough
```

### Usage (Manual Request)

```python
import httpx

# Send raw JSON directly
response = httpx.post(
    "http://127.0.0.1:8000/v1/messages",
    json={
        "model": "claude-opus-4-6",
        "max_tokens": 100,
        "messages": [
            {"role": "user", "content": "Hello"}
        ]
    },
    headers={
        "Authorization": "Bearer sk-ant-...",
        "Content-Type": "application/json"
    }
)

print(response.json())
```

### Use Cases

- **Testing:** Debug proxy behavior without SDK
- **Custom providers:** Route to non-standard endpoints
- **Experimentation:** Send raw API requests

---

## Choosing the Right Adapter

### Use Anthropic if:
- ✅ Primary provider is Claude
- ✅ You want the most mature integration
- ✅ Starting a new project

### Use OpenAI Chat if:
- ✅ Primary provider is OpenAI (GPT-4, GPT-3.5)
- ✅ Need function calling or JSON mode
- ✅ Migrating from OpenAI SDK

### Use Google if:
- ✅ Primary provider is Gemini
- ✅ Want multimodal (vision) support
- ✅ Using Google's ecosystem

### Use Passthrough if:
- ✅ Debugging proxy issues
- ✅ Using a custom/unsupported provider
- ✅ Testing raw API behavior

---

## Configuration Examples

### Multi-Provider Fallback

```yaml
# Try Claude first, fall back to Gemini, then GPT-4
provider: anthropic
fallback:
  - google
  - openai

providers:
  anthropic:
    model: claude-opus-4-6
  google:
    model: gemini-pro
  openai:
    model: gpt-4o
```

### Cost-Optimized Routing

```yaml
# Use cheaper Haiku for simple tasks, Opus for complex
provider: anthropic
routing:
  simple_tasks: claude-haiku-3-5  # Cheaper
  complex_tasks: claude-opus-4-6  # More capable
```

---

## Error Handling

All adapters use the same error handling. See [Error Handling Guide](./error-handling.md).

```python
try:
    response = client.messages.create(...)
except TokenLimitError as e:
    print(f"Token limit: {e.message}")
except ProviderError as e:
    print(f"Provider error: {e.message}")
except Exception as e:
    print(f"Unknown error: {e}")
```

---

## Next Steps

- **Token counting:** See [Installation](./installation.md)
- **Error handling:** Check [Error Handling Guide](./error-handling.md)
- **Advanced routing:** See [Feature Matrix](./features.md) (PRO only)

All adapters work out-of-the-box with FREE TokenPak.
