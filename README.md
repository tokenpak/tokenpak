# TokenPak — Cut your LLM token spend by 30–50%, zero config

[![PyPI version](https://img.shields.io/pypi/v/tokenpak.svg)](https://pypi.org/project/tokenpak/)
[![Python 3.10+](https://img.shields.io/pypi/pyversions/tokenpak.svg)](https://pypi.org/project/tokenpak/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
<!-- CI badge: pending repo transfer to tokenpak/tokenpak — add after transfer is confirmed -->

TokenPak is a local proxy that compresses your LLM context before it hits the API — fewer tokens, lower cost, same results. No code changes, no cloud, no credentials stored.

---

## 30-second demo

```bash
pip install tokenpak
tokenpak serve                          # start proxy at localhost:8766
tokenpak integrate claude-code --apply  # wire Claude Code to the proxy
```

```
✅ Applied: Updated ~/.claude/settings.json (2 changes).
```

Then verify it's working:

```bash
tokenpak demo
```

```
┌──────────────────────────────────────────────────────┐
│  TokenPak — Live Compression Demo                    │
├──────────────────────────────────────────────────────┤
│  Scenario              DevOps agent (config + logs)  │
│  Savings drivers                      dedup + alias  │
├──────────────────────────────────────────────────────┤
│  Original                                747 tokens  │
│  Compressed                              502 tokens  │
│  Saved                          245 tokens  (32.8%)  │
│  Cost saved (est.)                $0.00073 per call  │
├──────────────────────────────────────────────────────┤
│  Stages: dedup, alias, segmentize, directives        │
└──────────────────────────────────────────────────────┘
```

---

## Works with

**Claude Code** · **Cursor** · **Cline** · **Continue.dev** · **Aider** · **OpenAI SDK** · **Anthropic SDK** · **LiteLLM** · **Codex**

Run `tokenpak integrate` to see the full client list with setup guides for each.

---

## Install

```bash
pip install tokenpak
```

See [docs/quickstart.md](docs/quickstart.md) for virtual-env setup and per-client configuration.

Requirements: Python 3.10+. No external dependencies for core functionality.

---

## What's included (Free)

- **Context compression** — 30–50% token reduction on real agent workloads, <50ms latency
- **Client integration** — one command wires Claude Code, Cursor, Aider, and 6 other clients
- **Model routing** — send requests to the right model automatically, with fallback rules
- **Cost tracking** — per model, per session, per agent; local SQLite, zero cloud
- **Vault indexing + semantic search** — index your codebase; search without an LLM call
- **CLI + proxy server** — `tokenpak serve`, `tokenpak cost`, `tokenpak savings`
- **A/B testing and replay/debug** — compare compression configs, replay past requests
- **50 built-in compression recipes** — YAML, customizable

80%+ of operations cost zero tokens. See [docs/quickstart.md](docs/quickstart.md) and [docs/api-tpk-v1.md](docs/api-tpk-v1.md) to get started.

---

## Pricing

| | Free | Pro | Team |
|--|:--:|:--:|:--:|
| Context compression | ✅ | ✅ | ✅ |
| Client integration (all 9) | ✅ | ✅ | ✅ |
| Model routing | ✅ | ✅ | ✅ |
| Cost tracking | ✅ | ✅ | ✅ |
| Vault indexing + search | ✅ | ✅ | ✅ |
| CLI + proxy | ✅ | ✅ | ✅ |
| Advanced compression recipes | — | ✅ | ✅ |
| Budget enforcement + alerts | — | ✅ | ✅ |
| Priority support | — | ✅ | ✅ |
| Multi-agent coordination | — | — | ✅ |
| Shared vault (team) | — | — | ✅ |
| RBAC + audit logs | — | — | ✅ |
| **Price** | **Free** | **$99/mo** | **$299/mo** |

See [tokenpak.ai/pricing](https://tokenpak.ai/pricing) for full tier details and enterprise options.

---

## Support

- **Docs:** [docs/quickstart.md](docs/quickstart.md) · [API reference](docs/api-tpk-v1.md)
- **Issues:** [github.com/tokenpak/tokenpak/issues](https://github.com/tokenpak/tokenpak/issues)
- **Discussions:** [github.com/tokenpak/tokenpak/discussions](https://github.com/tokenpak/tokenpak/discussions)
- **Email:** hello@tokenpak.ai
