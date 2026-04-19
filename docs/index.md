---
hide:
  - navigation
  - toc
---

# TokenPak

**The intelligent LLM proxy for context compression and vault injection.**

TokenPak sits between your AI tools and the upstream LLM API, automatically compressing context, injecting vault knowledge, and caching tokens to slash costs and latency.

---

## ✨ Key Features

- **Context compression** — Up to 40% token reduction on large payloads
- **Vault injection** — Automatically enrich requests with relevant knowledge
- **Token caching** — Reduce repeat API costs with smart cache hits
- **Drop-in proxy** — Works with any OpenAI-compatible client
- **Multi-provider** — Routes to Anthropic, OpenAI, Gemini, and more

---

## 🚀 Quick Start

```bash
pip install tokenpak
tokenpak start --port 8766
# Point your client to http://localhost:8766
```

→ [Full installation guide](installation.md)  
→ [Quick start guide](QUICKSTART.md)

---

## 📚 Documentation

| Section | Description |
|---------|-------------|
| [Getting Started](installation.md) | Install and configure TokenPak |
| [Configuration](configuration.md) | All configuration options |
| [CLI Reference](cli-reference.md) | Command-line interface |
| [Architecture](features.md) | How TokenPak works |
| [FAQ](faq.md) | Common questions |
