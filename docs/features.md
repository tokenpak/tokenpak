---
title: "features"
created: 2026-03-24T19:05:55Z
---
# Feature Matrix — FREE vs PRO

All core proxy features are **FREE and open source**. Advanced features are in the optional PRO package.

---

## Quick Reference

| Category | Feature | FREE | PRO | Notes |
|----------|---------|------|-----|-------|
| **Core Routing** | Multiple provider adapters | ✅ | ✅ | 5 adapters built-in |
| | Fallback chains | ✅ | ✅ | Auto-failover to backup providers |
| | Circuit breaker | ✅ | ✅ | Recovers from rate limits |
| | Custom routing rules | ❌ | ✅ | Cost-based, latency-based logic |
| **Token Management** | Token counting (all providers) | ✅ | ✅ | Unified across Anthropic, OpenAI, Google |
| | Cost tracking | ✅ | ✅ | Basic tracking + reporting |
| | Cost alerts | ❌ | ✅ | Notifications when exceeding budget |
| | Budget enforcement | ❌ | ✅ | Auto-reject requests exceeding limit |
| **Compression** | Deduplication | ✅ | ✅ | Remove repeated content |
| | Document compression | ✅ | ✅ | Summarize long docs |
| | Instruction table | ✅ | ✅ | Compress repetitive instructions |
| | Dictionary compression | ❌ | ✅ | Custom compression dictionaries |
| | Code/log extraction | ❌ | ✅ | Smart code and log parsing |
| | Alias compression | ❌ | ✅ | Shorten variable names, function calls |
| **Error Handling** | Normalized error messages | ✅ | ✅ | Consistent across providers |
| | Automatic retries | ✅ | ✅ | Exponential backoff, configurable |
| | Retry orchestration | ❌ | ✅ | Advanced retry strategies, decision trees |
| | Error telemetry | ✅ | ✅ | Log error types and frequency |
| **Observability** | Request/response logging | ✅ | ✅ | JSON logs, searchable |
| | Cost dashboard (web UI) | ❌ | ✅ | Real-time cost, savings, usage charts |
| | Token usage reports | ✅ | ✅ | CSV export, JSON export |
| | Live request explorer | ❌ | ✅ | Debug individual requests |
| **Agentic** | Error normalization | ✅ | ✅ | Convert errors to agent-readable format |
| | Streaming support | ✅ | ✅ | Handle streaming + non-streaming |
| | Function call translation | ❌ | ✅ | Convert between provider formats |
| | Tool/function use optimization | ❌ | ✅ | Smart function routing |
| **Vault Integration** | Document indexing | ✅ | ✅ | Index local files (.md, .txt, .pdf) |
| | Semantic search | ✅ | ✅ | Search vault by meaning |
| | Auto-injection | ✅ | ✅ | Automatically add relevant docs to context |
| | Symbol extraction | ✅ | ✅ | Extract functions, classes, variables |
| | AST parsing | ✅ | ✅ | Parse code structure |
| | Chunk optimization | ✅ | ✅ | Smart chunking for injection |
| | Watcher mode | ✅ | ✅ | Live re-index on file changes |
| **Framework Integration** | LangChain adapter | ❌ | ✅ | Drop-in replacement for LangChain |
| | CrewAI adapter | ❌ | ✅ | CrewAI integration |
| | AutoGen adapter | ❌ | ✅ | Microsoft AutoGen support |
| | Ollama integration | ❌ | ✅ | Local LLM support |
| **CLI** | `serve` command | ✅ | ✅ | Start the proxy |
| | `count` command | ✅ | ✅ | Count tokens in a file |
| | `compress` command | ✅ | ✅ | Test compression on a document |
| | `validate` command | ✅ | ✅ | Check config and connectivity |
| | `dashboard` command | ❌ | ✅ | Launch web dashboard |
| | `report` command | ✅ | ✅ | Generate usage reports |

---

## FREE Features in Detail

### Core Proxy

**R1-R12** — Provider routing, adapters, tool schema handling, fallback chains, circuit breaker, streaming, passthrough

```python
from tokenpak import Client

# Works out-of-the-box
client = Client(api_key="...", model="claude-opus-4-6")
response = client.messages.create(...)
```

### Token Counting

**T2** — Accurate token counts across all providers

```python
tokens = client.count_tokens(
    model="claude-opus-4-6",
    messages=[{"role": "user", "content": "..."}]
)
```

### Basic Compression

**C1, C2, C4, C8, C9, C10, C11, C13** — Deduplication, doc compression, instruction table, budget tracking, fidelity tiers

Automatically applied. Semantic equivalence guaranteed.

### Error Handling

**A3, A5** — Normalized errors, automatic retries with exponential backoff

```python
# Retries automatically with circuit breaker
response = client.messages.create(...)
# If provider fails, falls back to next in chain
```

### Vault Features

**V1-V8** — Indexing, search, auto-injection, symbol extraction, AST parsing, chunking, watcher, SQLite backend

```yaml
vault:
  enabled: true
  root: "~/my-vault"
  auto_inject: true  # Automatically add relevant docs
```

---

## PRO Features in Detail (Upgrade Path)

### Advanced Compression

- **Dictionary compression** (C7) — Custom compression tables for your domain
- **Code extraction** (C5) — Smart parsing and compression of code blocks
- **Log extraction** (C6) — Compress repetitive logs
- **Alias compression** (C8) — Shorten variable names intelligently

### Cost Management

- **Budget enforcement** — Reject requests exceeding limit
- **Cost alerts** — Email/webhook when approaching budget
- **Advanced routing** — Route by cost, latency, capability

### Dashboard

- Real-time cost tracking
- Token usage charts
- Request history explorer
- Model comparison (cost vs latency)
- Savings calculator

### Framework Adapters

- LangChain integration (drop-in replacement)
- CrewAI support
- Microsoft AutoGen
- Ollama (local LLM)
- Custom adapter builder

### Advanced Agentic

- Function call optimization
- Retry orchestration (conditional retries based on error type)
- Capability-aware routing (route by function support)

---

## Choosing FREE vs PRO

### Use FREE if you:

- ✅ Building a prototype or MVP
- ✅ Want open source with no licensing headaches
- ✅ Need multi-provider routing (core feature)
- ✅ Want to understand how it works
- ✅ Running on a budget

### Upgrade to PRO if you:

- 🚀 Running in production with high volume
- 🚀 Need advanced compression (30-60% more savings)
- 🚀 Want budget enforcement (cost control)
- 🚀 Need dashboard (real-time monitoring)
- 🚀 Using frameworks (LangChain, CrewAI)
- 🚀 Want priority support

---

## Installation & Usage

### FREE Package

```bash
pip install tokenpak
tokenpak serve
```

### PRO Package

```bash
pip install tokenpak-pro
# (includes all FREE features + advanced ones)
tokenpak serve --pro
```

Both can run side-by-side. PRO seamlessly upgrades FREE.

---

## Configuration Reference

### Basic config.yaml (FREE)

```yaml
proxy:
  port: 8000

provider: anthropic
fallback:
  - google
  - openai

compression:
  enabled: true

telemetry:
  enabled: true
  log_file: /tmp/tokenpak.log

vault:
  enabled: true
  root: ~/my-vault
```

### Advanced config.yaml (PRO)

Adds:
- `routing.strategy` (cost, latency, capability)
- `budget.limit` (enforce spending cap)
- `alerts.webhook` (cost alerts)
- `compression.custom_dictionaries` (domain-specific)
- `framework.langchain` (enable LangChain integration)

---

## Feature Roadmap

### Coming Soon (FREE)
- Multi-turn conversation history management
- Prompt caching integration
- Vision/multimodal support

### Coming Soon (PRO)
- Fine-tuning pipeline integration
- Advanced batch processing
- Team/workspace management

---

## Support & Licensing

**FREE** — MIT License. Use however you like.

**PRO** — Commercial license. Support options available.

See [README](index.md) for more information.
