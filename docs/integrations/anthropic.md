# TokenPak + Anthropic

Route all Anthropic API calls through TokenPak for automatic compression and cost tracking.

## Quick Setup

```bash
pip install tokenpak anthropic
tokenpak serve  # starts proxy on localhost:8766
```

## Drop-in Replacement

```python
import anthropic

# One-line change — swap base_url
client = anthropic.Anthropic(
    api_key="sk-ant-...",
    base_url="http://localhost:8766",
)

# All standard calls work unchanged
message = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Summarize this document."}],
)
print(message.content[0].text)
```

## Streaming

```python
with client.messages.stream(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Write a short story."}],
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

## Compression Modes

Control compression per-request via headers:

```python
import httpx

# Use anthropic with custom HTTP client for header control
http_client = httpx.Client(
    headers={"x-tokenpak-mode": "aggressive"},  # aggressive | hybrid | off
)
client = anthropic.Anthropic(
    api_key="sk-ant-...",
    base_url="http://localhost:8766",
    http_client=http_client,
)
```

| Mode | Description | Savings |
|------|-------------|---------|
| `hybrid` | Smart compression (default) | ~40-60% |
| `aggressive` | Maximum compression | ~60-80% |
| `off` | Passthrough only | 0% |

## Config Example

`~/.tokenpak/config.toml`:
```toml
[proxy]
port = 8766
mode = "hybrid"

[providers.anthropic]
api_key_env = "ANTHROPIC_API_KEY"
models = ["claude-*"]

[compression]
protect_system_prompts = true
preserve_code_blocks = true
target_ratio = 0.5
```

## Verify It's Working

```python
import httpx
stats = httpx.get("http://localhost:8766/stats").json()
print(f"Tokens saved: {stats['session']['saved_tokens']}")
print(f"Cost saved: ${stats['session']['cost_saved']:.4f}")
```

## Troubleshooting

- **Auth errors** → Check `ANTHROPIC_API_KEY` is set; proxy forwards it upstream
- **Connection refused** → Run `tokenpak serve` first or check `systemctl --user status tokenpak-proxy`
- **Slow first request** → Vault index loading; subsequent requests are faster

See also: [Troubleshooting Guide](../troubleshooting.md) · [Error Reference](../errors.md)
