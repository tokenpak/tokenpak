---
title: "README"
created: 2026-03-24T19:05:55Z
---
# TokenPak — FREE Package Documentation

Welcome to TokenPak! This documentation covers the **free, open-source package** (`tokenpak` on PyPI).

---

## What is TokenPak?

TokenPak is a **provider-agnostic proxy layer** that sits between your application and LLM providers (Anthropic, OpenAI, Google, etc.). It lets you write once and route flexibly, automatically count tokens across providers, and compress context to reduce API costs.

### Key Capabilities

✅ **Smart Adapter System** — Convert between provider request/response formats seamlessly
✅ **Token Counting** — Accurate token counts across all supported providers
✅ **Fallback Chains** — Specify backup providers if your primary goes down
✅ **Circuit Breaker** — Automatic recovery from rate limits and transient failures
✅ **Streaming Support** — First-class support for streaming and non-streaming responses
✅ **Compression Suite** — Document, instruction, and deduplication compression
✅ **Error Handling** — Normalized error messages across all providers
✅ **Cost Tracking** — Basic token usage and cost tallying
✅ **Vault Integration** — Automatically inject and index local documents

**Goal:** Get the proxy running quickly, understand the patterns, and customize from there.

---

## Quick Links

- **[Architecture](./architecture.md)** — How TokenPak works under the hood
- **[Installation Guide](./installation.md)** — Get TokenPak running in 5 minutes
- **[Feature Matrix](./features.md)** — What's included in FREE vs PRO
- **[Adapter Reference](./adapters.md)** — All 5 adapters with code examples
- **[Error Handling Guide](./error-handling.md)** — Common issues and solutions
- **[Quick Start](./QUICKSTART.md)** — 30-second working example
- **[Observability](./observability.md)** — Monitor your proxy activity

---

## 30-Second Example

```python
from tokenpak import Client

# 1. Create a client
client = Client(
    api_key="sk-...",  # Your API key
    model="claude-opus-4-6"  # Default model
)

# 2. Make a request (routed through the proxy)
response = client.messages.create(
    model="claude-opus-4-6",
    messages=[{"role": "user", "content": "What is 2+2?"}],
    max_tokens=100
)

print(response.content[0].text)
```

That's it. Your request went through TokenPak's proxy, which handled:
- Token counting
- Cost tracking
- Error handling
- Adapter translation (if needed)

---

## How It Works (Conceptually)

```
┌─────────────┐
│  Your Code  │
└──────┬──────┘
       │
       ▼
┌──────────────────────────────────────┐
│  TokenPak Proxy (port 8000)          │
│  ├─ Router (which model?)            │
│  ├─ Adapter (format conversion)      │
│  ├─ Compression (save tokens)        │
│  ├─ Error handler (normalize errors) │
│  └─ Telemetry (track costs)          │
└──────┬───────────────────────────────┘
       │
       ├──────────────▶ OpenAI
       ├──────────────▶ Anthropic (Claude)
       ├──────────────▶ Google (Gemini)
       └──────────────▶ Azure / Other
```

**What happens in a typical request:**

1. **Your code** sends a message to the TokenPak proxy
2. **Router** determines the best provider (default or custom logic)
3. **Adapter** converts your request to the provider's format
4. **Compression** trims unnecessary context (optional, free)
5. **Request is sent** to the actual provider
6. **Response** is converted back to your format
7. **Telemetry** logs tokens, cost, and latency
8. **Your code** gets a consistent response format

---

## Core Concepts

### Adapters

An **adapter** is a converter between your code's format and a provider's format. TokenPak includes 5 adapters:

| Adapter | Converts To | Status |
|---------|-------------|--------|
| `anthropic` | Claude API format | ✅ |
| `openai_chat` | OpenAI Chat format | ✅ |
| `openai_responses` | OpenAI Responses (legacy) | ✅ |
| `google` | Google Gemini format | ✅ |
| `passthrough` | Raw JSON (debugging) | ✅ |

See [Adapter Reference](./adapters.md) for examples.

### Fallback Chains

You can specify multiple providers in order of preference. If the primary provider fails, TokenPak automatically tries the next:

```yaml
providers:
  - anthropic       # Try Claude first
  - google          # Fall back to Gemini
  - openai_chat     # Then OpenAI
```

See [Error Handling Guide](./error-handling.md) for circuit breaker logic.

### Compression

TokenPak can automatically reduce token usage by:

- **Deduplication** — Remove repeated sections
- **Document compression** — Summarize long documents
- **Instruction table** — Compress repetitive instruction blocks
- **Fingerprinting** — Reference cached content instead of re-sending

All compression is **optional and safe** — results are always semantically equivalent.

### Token Counting

TokenPak counts tokens accurately for all providers:

```python
tokens = client.count_tokens(
    model="claude-opus-4-6",
    messages=[{"role": "user", "content": "..."}]
)
print(f"This message is {tokens} tokens")
```

---

## Installation & Setup

### 5-Minute Setup

1. **Install:** `pip install tokenpak`
2. **Set API key:** `export ANTHROPIC_API_KEY=sk-...`
3. **Run:** `tokenpak serve`
4. **Test:** curl or Python client (see [Installation Guide](./installation.md))

---

## Getting Help

- **Questions?** → Check [Error Handling Guide](./error-handling.md) or the [Troubleshooting Guide](./TROUBLESHOOTING.md)
- **Found a bug?** → Open an issue on GitHub
- **Want to contribute?** → PRs welcome!

---

## Next Steps

- **New to TokenPak?** → Read [Installation Guide](./installation.md)
- **Want to use a specific provider?** → See [Adapter Reference](./adapters.md)
- **Setting up for production?** → Check [Error Handling](./error-handling.md) for circuit breakers
- **Curious about features?** → [Feature Matrix](./features.md)

---

## License

TokenPak is distributed under the **Apache-2.0 License**. Use it however you like.
