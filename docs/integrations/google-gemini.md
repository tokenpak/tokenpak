# TokenPak + Google Gemini

Route Google Gemini API calls through TokenPak for compression and unified cost tracking.

## Quick Setup

```bash
pip install tokenpak google-generativeai
tokenpak serve  # starts proxy on localhost:8766
```

## Using the REST API Directly

Gemini's Python SDK doesn't support `base_url` overrides directly. Use `httpx` or `requests`:

```python
import httpx
import json

PROXY_URL = "http://localhost:8766/v1/google/gemini"
API_KEY = "AIza..."  # Your Gemini API key

def generate(prompt: str, model: str = "gemini-2.0-flash") -> str:
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
    }
    r = httpx.post(
        f"{PROXY_URL}/models/{model}:generateContent",
        headers={"x-goog-api-key": API_KEY},
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]

print(generate("Explain transformer attention in one paragraph."))
```

## Using LiteLLM (Recommended)

LiteLLM gives a unified interface with full `base_url` support:

```bash
pip install litellm
```

```python
import litellm

# Route through TokenPak
litellm.api_base = "http://localhost:8766"

response = litellm.completion(
    model="gemini/gemini-2.0-flash",
    messages=[{"role": "user", "content": "What is quantum entanglement?"}],
    api_key="AIza...",
)
print(response.choices[0].message.content)
```

See [LiteLLM integration guide](./litellm.md) for full details.

## Config Example

```toml
[providers.google]
api_key_env = "GEMINI_API_KEY"
models = ["gemini-*"]
base_url = "https://generativelanguage.googleapis.com"

[compression]
protect_system_prompts = true
target_ratio = 0.5
```

## Supported Models

| Model | Notes |
|-------|-------|
| `gemini-2.0-flash` | Fast, cost-efficient |
| `gemini-2.0-pro` | High-capability |
| `gemini-1.5-flash` | Legacy fast |
| `gemini-1.5-pro` | Legacy pro |

## Verify It's Working

```bash
curl -s http://localhost:8766/stats | python3 -c "
import json, sys
s = json.load(sys.stdin)['session']
print(f'Requests: {s[\"requests\"]}')
print(f'Tokens saved: {s[\"saved_tokens\"]}')
"
```

## Troubleshooting

- **404 on model routes** → Confirm proxy version supports Google passthrough (`tokenpak --version`)
- **Auth errors** → Set `GEMINI_API_KEY` env var; proxy reads it automatically
- **Rate limits** → TokenPak retries automatically up to 3 times with exponential backoff

See also: [Troubleshooting Guide](../troubleshooting.md) · [Error Reference](../errors.md)
