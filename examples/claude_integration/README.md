# Claude (Anthropic) API Integration

**Problem:** Claude's API charges per input token. Long conversation histories with verbose messages burn budget.

**Solution:** `TokenPakAnthropicMessages` wraps the Anthropic messages API, compressing older turns before forwarding to Claude.

## What This Shows

- Anthropic-specific message compression (handles Claude's message format)
- Sliding window: recent turns untouched, older turns compressed
- System prompt stays uncompressed (behavior-critical)
- Stats tracking per session

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
```

## Run

```bash
python main.py
```

## Migration

```python
# Before
import anthropic
client = anthropic.Anthropic()
response = client.messages.create(
    model="claude-opus-4-5",
    messages=messages,
    system=system_prompt,
    max_tokens=1024,
)

# After
from main import TokenPakAnthropicMessages
client = TokenPakAnthropicMessages(target_tokens_per_msg=500)
response = client.create(
    messages=messages,
    system=system_prompt,
    model="claude-opus-4-5",
    max_tokens=1024,
)
```

## Token Budget Impact

| Turns | Without TokenPak | With TokenPak | Savings |
|---|---|---|---|
| 10 | ~3,000 | ~1,800 | 40% |
| 20 | ~6,000 | ~2,900 | 52% |
| 50 | ~15,000 | ~5,500 | 63% |

## Time to Complete

~10 minutes
