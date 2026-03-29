---
title: "TokenPak Documentation"
---

# TokenPak

**Provider-agnostic LLM proxy with smart context compression.**

TokenPak sits between your application and LLM providers (Anthropic, OpenAI, Google, etc.), giving you unified routing, token counting, context compression, and cost tracking — without changing your existing code.

---

## Get Started Fast

- **[Installation](installation.md)** — Get running in 5 minutes
- **[Quick Start](QUICKSTART.md)** — 30-second working example
- **[Comparison](comparison.md)** — TokenPak vs LiteLLM vs raw API
- **[Migration](migration.md)** — Migrate from direct API calls

---

## Key Features

✅ **Smart Routing** — Provider-agnostic with fallback chains and circuit breakers
✅ **Context Compression** — Reduce token usage automatically
✅ **Token Counting** — Accurate counts across all providers
✅ **Cost Tracking** — Real-time usage and budget monitoring
✅ **Streaming** — First-class streaming support
✅ **Vault Integration** — Inject and index local documents

---

## Quick Install

```bash
pip install tokenpak
tokenpak serve
```

Point your app at `http://localhost:8766` — done.

---

## Resources

- [GitHub](https://github.com/tokenpak/tokenpak)
- [PyPI](https://pypi.org/project/tokenpak/)
- [API Reference](api-reference.md)
- [Error Codes](ERROR_CODES.md)
- [FAQ](FAQ.md)
