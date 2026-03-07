# OpenAI API Wrapper with TokenPak

**Problem:** You're calling `openai.chat.completions.create()` and want automatic compression without changing your existing code.

**Solution:** Swap the OpenAI client for `TokenPakOpenAI` — a drop-in replacement that compresses messages transparently before forwarding them.

## What This Shows

- Drop-in `OpenAI` client replacement
- Transparent compression on every API call
- Sliding window: recent turns preserved, older turns compressed
- Built-in stats tracking (tokens saved, cache hits)

## Setup

```bash
pip install -r requirements.txt
export OPENAI_API_KEY="sk-..."
```

## Run

```bash
python main.py
```

## Migration

```python
# Before
from openai import OpenAI
client = OpenAI(api_key="sk-...")

# After (one line change)
from main import TokenPakOpenAI
client = TokenPakOpenAI(api_key="sk-...", target_tokens=1000)

# Usage is identical
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

## Verbose Mode

```python
client = TokenPakOpenAI(verbose=True)
# Prints per-message compression stats:
# [TokenPak] Compressing 4 messages...
#   [user]      142 → 68 tokens (74 saved)
#   [assistant] 118 → 55 tokens (63 saved)
# [TokenPak] 340 → 183 tokens (46% savings)
```

## Stats

```python
print(client.stats)
# {"calls": 5, "tokens_saved": 342, "cache_hits": 2}
```

## Time to Complete

~10 minutes
