# TokenPak Quick Start Guide

Get from zero to savings in 5 minutes. Pick your path:

| Path | Best for |
|------|----------|
| [**Proxy Path**](#proxy-path-zero-config-optimization) | Existing apps — drop-in optimization, no code changes |
| [**SDK Path**](#sdk-path-protocol-first) | New projects or when you want protocol-level control |

---

## Proxy Path: Zero-Config Optimization

**You already write prompts. TokenPak compresses them before they hit the API.**

### Minute 1: Install

```bash
pip install tokenpak
```

### Minute 2: Start the proxy

```bash
tokenpak start
# → ✅ Proxy running on http://localhost:8766
```

### Minute 3: Point your app at the proxy

Change your LLM client's base URL to `http://localhost:8766`. That's it. No other code changes.

### Minute 4: See your savings

```bash
tokenpak demo     # see compression in action on a sample prompt
tokenpak cost     # view today's spend and tokens saved
```

That's it. Every request is now compressed automatically.

---

## SDK Path: Protocol-First

**Use the TokenPak format with any LLM client — no proxy needed.**

### Install

```bash
pip install tokenpak-sdk
```

### Compress and send

```python
from tokenpak import TokenPak, Block

pack = TokenPak(budget=4000)
pack.add_instructions("You are a helpful assistant.")
pack.add_knowledge("docs", "Your long documentation here...")
pack.add_conversation([{"role": "user", "content": "Summarize the docs"}])

# Works with any OpenAI-compatible client
from openai import OpenAI
client = OpenAI()
response = client.chat.completions.create(
    model="gpt-4",
    messages=pack.to_messages()
)

# See how much was saved
print(pack.compile().report)
# → Input: 8,420 tokens → Output: 3,200 tokens | Savings: 62%
```

---

## Common Use Cases

### "I use Claude Code"

Claude Code uses an OpenAI-compatible API. Point it at the proxy:

```bash
# Start the proxy
tokenpak start

# Set the base URL in your Claude Code config or environment:
export ANTHROPIC_BASE_URL=http://localhost:8766
```

All requests are automatically compressed before reaching Anthropic. No code changes needed.

### "I use the OpenAI SDK"

```python
from openai import OpenAI

# Just change base_url — everything else stays the same
client = OpenAI(
    api_key="your-openai-key",
    base_url="http://localhost:8766"
)

response = client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Your prompt here"}]
)
```

### "I use LangChain"

```python
from langchain_openai import ChatOpenAI

# Point LangChain at the proxy
llm = ChatOpenAI(
    model="gpt-4",
    openai_api_base="http://localhost:8766",
    openai_api_key="your-key"
)

response = llm.invoke("Your prompt here")
```

### "I use LiteLLM / other frameworks"

Most frameworks support a `base_url` or `api_base` parameter. Set it to `http://localhost:8766`.

---

## Troubleshooting

### "It's not connecting"

```bash
tokenpak status    # is the proxy running?
tokenpak start     # start it if not
```

Check that your client is pointing at `http://localhost:8766` (not `https://`).

### "My API key isn't being forwarded"

TokenPak is a passthrough proxy — it never stores or modifies your credentials. Make sure:
- Your API key is set in your environment: `export ANTHROPIC_API_KEY='sk-...'`
- Or pass it directly in your client config

### "I'm not seeing any savings"

```bash
tokenpak cost --week    # check a longer time window
tokenpak demo           # verify compression is working
```

Short prompts compress less. Savings show up most on long conversations and large document contexts.

### "The proxy started but requests aren't going through"

Verify your client is using the right port:
```bash
curl http://localhost:8766/health
# → {"status": "ok", ...}
```

If the health check fails, restart with `tokenpak restart`.

---

## What Do I Do Next?

- **Check your savings:** `tokenpak cost --week`
- **Tune compression:** See [compression.md](./compression.md) for aggressiveness settings
- **Index your vault:** `tokenpak index ~/your-docs` for semantic search at zero token cost
- **Full CLI reference:** [cli-reference.md](./cli-reference.md) — all commands explained
- **API reference:** [api-reference.md](./api-reference.md) — SDK classes and methods

---

> **Tip:** Run `tokenpak demo` at any time to see live compression on a sample prompt — no proxy needed.
