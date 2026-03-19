# 📦 TokenPak

> **One proxy between your code and the LLM API. Optimize cache hits, compress context, route smart, track every dollar.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-213%20passing-brightgreen.svg)](#testing)

TokenPak sits between your code and the LLM API. It packs your tokens before they leave your machine — optimizing cache hits, compressing context, and routing requests — then tracks every dollar so you know exactly what you're spending.

**Everything runs on your machine.** No cloud. No accounts. No data leaves except the (optimized) API call.

---

## Where The Savings Come From

### 🧊 Cache Optimization

LLM providers discount repeated prompt prefixes. But most SDKs serialize tool schemas and system prompts with non-deterministic key ordering — different bytes every request, cache miss every time.

TokenPak's **Tool Schema Registry** normalizes everything into identical bytes. Same tools, same bytes, cache hit. On an agent with 20+ tools sending 10-20KB of schemas per request, this is the difference between paying full price and paying 10%.

### 🔀 Smart Routing

Not every request needs your most expensive model. TokenPak routes by pattern, token count, or intent — sending simple tasks to cheaper models while keeping complex work on the heavy hitters. Automatic fallback chains mean nothing breaks.

### 📦 Token Compression

Multi-stage pipeline strips redundancy from your context:

1. **Segment** — split into semantic blocks
2. **Fingerprint** — detect type (code, docs, config, logs)
3. **Compress** — apply type-aware recipes
4. **Budget** — allocate tokens by priority
5. **Assemble** — rebuild with fewer tokens

Content-aware: code gets AST-level compression (tree-sitter), docs get section-aware trimming, JSON/YAML gets schema extraction, logs get pattern dedup. 20-60% reduction depending on content type.

---

## Quick Start

```bash
pip install tokenpak
tokenpak serve --port 8766
```

Change one line in your code:

### Anthropic (Claude)

```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://localhost:8766",  # 📦 that's it
    api_key="sk-ant-..."              # passes straight through
)

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Explain quantum computing"}]
)
# → Cache optimized. Compressed. Cost tracked. Automatically.
```

### OpenAI

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8766", api_key="sk-...")
```

### Claude Code / OpenClaw

```bash
export ANTHROPIC_BASE_URL=http://localhost:8766
# done. every request now goes through TokenPak.
```

### Docker

```bash
git clone https://github.com/tokenpak/tokenpak.git && cd tokenpak
cp .env.example .env        # add your API key
docker compose -f docker/docker-compose.yml up -d
```

---

## See What You're Spending

```bash
tokenpak savings
```

Shows cost breakdown by model, cache hit rates, and what TokenPak saved you — updated in real time from your actual usage.

Web dashboard at `http://localhost:8766/dashboard`:

- **FinOps** — cost by model, savings trends, spend forecasting
- **Engineering** — latency, cache hit rates, compression ratios
- **Audit** — trace any request through the full pipeline
- **Executive Summary** — top-line numbers for reporting
- **Export** — CSV and JSON

All local. All your data. Nothing phones home.

---

## Everything It Does

| Feature | What | Why |
|---|---|---|
| 🧊 **Cache Optimization** | Deterministic tool schema serialization | 86% of savings in production |
| 🔀 **Smart Routing** | Route by model, pattern, intent, token count | Right model for the job, automatic failover |
| 📦 **Compression** | Content-aware pipeline (code, docs, data, logs) | 20-60% fewer tokens per request |
| 💰 **Cost Tracking** | Per-request, per-model, per-session pricing | Know exactly what you spend |
| 📊 **Dashboard** | 10-page web UI with FinOps/Engineering/Audit views | See everything, export anything |
| 🔍 **Vault Indexing** | Semantic search over your codebase | Zero-token search — never calls an LLM |
| 🧪 **A/B Testing** | Compare strategies with statistical significance | Data-driven optimization |
| 👻 **Shadow Mode** | Validate compression without affecting production | Safe to try, safe to ship |
| 🚨 **Budget Enforcement** | Limits + alerts per session, model, or agent | Never blow your budget |
| 🛡️ **DLP Scanning** | Detect and redact sensitive data | PII stays on your machine |
| 🔌 **Data Connectors** | Local, Git, Obsidian, GitHub, Google Drive, Notion | Index any knowledge source |
| ⚡ **+2ms Latency** | Sub-millisecond compression, minimal proxy overhead | You won't notice it |

---

## Compatibility

| Platform | How |
|---|---|
| **Anthropic SDK** | `base_url="http://localhost:8766"` |
| **OpenAI SDK** | `base_url="http://localhost:8766"` |
| **Google AI (Gemini)** | Proxy adapter |
| **Claude Code** | `export ANTHROPIC_BASE_URL=http://localhost:8766` |
| **OpenClaw** | Set provider `base_url` to proxy |
| **Cursor** | Custom API endpoint in settings |
| **LiteLLM** | Drop-in middleware or proxy |
| **LangChain** | `from tokenpak.adapters.langchain import LangChainAdapter` |
| **Ollama** | Compression + routing (no cost tracking for local) |
| **curl / httpx / requests** | Standard REST API |

---

## CLI

```bash
# Run
tokenpak serve --port 8766          # start local proxy
tokenpak status                     # health check
tokenpak doctor                     # diagnose issues

# Monitor
tokenpak cost --week                # cost report by model
tokenpak savings                    # what you've saved

# Compress
tokenpak compress <file>            # dry-run compression
tokenpak diff <file>                # before/after comparison
tokenpak demo                       # see pipeline on sample data

# Search
tokenpak index <path>               # index a directory
tokenpak vault search "query"       # semantic search (zero tokens)

# Route
tokenpak route add --model 'gpt-4*' --target anthropic/claude-sonnet-4
tokenpak route list

# Debug
tokenpak trace --id <id>            # inspect pipeline run
tokenpak replay <id>                # replay past request
```

30+ commands. Full reference: [`docs/cli-reference.md`](docs/cli-reference.md)

---

## Architecture

```
┌──────────────┐     ┌──────────────────────────────────────────┐     ┌──────────────┐
│  Your Code   │────▶│  📦 TokenPak Proxy (:8766)               │────▶│  LLM API     │
│  (any SDK)   │◀────│  Runs on YOUR machine                    │◀────│              │
└──────────────┘     │                                          │     └──────────────┘
                     │  Cache Opt.    Routing      Telemetry    │
                     │  Compression   Budget       Dashboard    │
                     │  A/B Testing   Shadow       DLP Scan     │
                     │  Schema Reg.   Circuit Brk  Conn Pool    │
                     └──────────────────────────────────────────┘
                                        │
                     ┌──────────────────────────────────────────┐
                     │  📁 Local Storage (never leaves)         │
                     │  SQLite telemetry · Vault index · Cache  │
                     └──────────────────────────────────────────┘
```

---

## Configuration

```json
{
  "proxy": {
    "port": 8766,
    "passthrough_url": "https://api.anthropic.com"
  },
  "compression": {
    "enabled": true,
    "level": "balanced"
  },
  "budget": {
    "monthly_usd": null,
    "alert_at_pct": 80
  }
}
```

Pre-built configs: `anthropic-only` · `openai-only` · `cost-saving-max` · `local-ollama` · `privacy-first` · `mixed-routing` · `single-user` · `team-internal`

---

## Deployment

| Platform | Guide |
|----------|-------|
| pip | `pip install tokenpak && tokenpak serve` |
| Docker Compose | [`docker/`](docker/) |
| Kubernetes | [`deployments/k8s/`](deployments/k8s/) |
| AWS ECS | [`deployments/aws-ecs/`](deployments/aws-ecs/) |
| GCP Cloud Run | [`deployments/gcp-cloud-run/`](deployments/gcp-cloud-run/) |
| systemd | [`tokenpak/agent/systemd/`](tokenpak/agent/systemd/) |

---

## Testing

```bash
pip install -e ".[dev]"
pytest tests/ -q           # 213 tests
```

---

## Docs

[Installation](docs/installation.md) · [Configuration](docs/configuration.md) · [CLI Reference](docs/cli-reference.md) · [Architecture](docs/architecture.md) · [API Reference](docs/api-reference.md) · [Error Codes](docs/error-codes.md) · [Troubleshooting](docs/troubleshooting.md) · [Security](docs/SECURITY.md)

---

## Protocol

TokenPak implements the [TokenPak Protocol v1.0](schemas/):
[Block Schema](schemas/tokenpak-block-v1.0.json) · [Compiled Artifact](schemas/tokenpak-compiled-v1.0.json) · [Evidence Pack](schemas/tokenpak-evidence-v1.0.json)

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

```bash
git clone https://github.com/tokenpak/tokenpak && cd tokenpak
pip install -e ".[dev]" && pytest tests/ -q
```

---

## License

[MIT](LICENSE)

---

