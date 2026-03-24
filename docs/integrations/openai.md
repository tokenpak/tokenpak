# TokenPak + OpenAI

Route OpenAI API calls through TokenPak for automatic compression and cost tracking.

## Quick Setup

```bash
pip install tokenpak openai
tokenpak serve  # starts proxy on localhost:8766
```

## Drop-in Replacement

OpenAI's Python SDK supports `base_url` natively:

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-...",
    base_url="http://localhost:8766/v1",  # ONE LINE CHANGE
)

# All standard calls work unchanged
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Summarize this document."}],
)
print(response.choices[0].message.content)
```

## Streaming

```python
with client.chat.completions.stream(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Write a short story."}],
) as stream:
    for event in stream:
        if event.choices and event.choices[0].delta.content:
            print(event.choices[0].delta.content, end="", flush=True)
```

## Embeddings

```python
result = client.embeddings.create(
    model="text-embedding-3-small",
    input="TokenPak compresses LLM context automatically.",
)
print(f"Embedding dim: {len(result.data[0].embedding)}")
```

## Compression Modes

```python
import httpx
from openai import OpenAI

client = OpenAI(
    api_key="sk-...",
    base_url="http://localhost:8766/v1",
    http_client=httpx.Client(
        headers={"x-tokenpak-mode": "aggressive"},  # aggressive | hybrid | off
    ),
)
```

## Config Example

`~/.tokenpak/config.toml`:
```toml
[proxy]
port = 8766
mode = "hybrid"

[providers.openai]
api_key_env = "OPENAI_API_KEY"
models = ["gpt-4*", "gpt-3.5*", "o1*", "text-embedding*"]

[compression]
protect_system_prompts = true
preserve_code_blocks = true
```

## Custom Adapter Pattern

For apps that can't change the `base_url` directly, set via environment:

```bash
# Override OpenAI base URL at the environment level
export OPENAI_BASE_URL="http://localhost:8766/v1"

# All openai SDK calls now route through TokenPak
python your_app.py
```

## Verify It's Working

```python
from openai import OpenAI
import httpx

client = OpenAI(api_key="sk-...", base_url="http://localhost:8766/v1")
client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Say hello."}],
    max_tokens=5,
)

stats = httpx.get("http://localhost:8766/stats").json()["session"]
print(f"Requests proxied: {stats['requests']}")
print(f"Tokens saved: {stats['saved_tokens']}")
```

## Troubleshooting

- **401 Unauthorized** → Verify `OPENAI_API_KEY` is exported; proxy forwards it upstream
- **Model not found** → Ensure model name matches exactly (e.g. `gpt-4o` not `gpt4o`)
- **Timeout on long requests** → Increase timeout: `httpx.Client(timeout=120)`

See also: [Troubleshooting Guide](../troubleshooting.md) · [Error Reference](../errors.md)
