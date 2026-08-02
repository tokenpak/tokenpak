# TokenPak — local context packing with measurable receipts

> **The open logistics layer for AI context.**

TokenPak is a local proxy that packs AI requests before they ship and records
what changed. It helps developers reduce repeated context on eligible routes,
without moving prompts or credentials into a TokenPak cloud service.

[![CI](https://github.com/tokenpak/tokenpak/actions/workflows/ci.yml/badge.svg)](https://github.com/tokenpak/tokenpak/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/tokenpak.svg)](https://pypi.org/project/tokenpak/)
[![Python 3.10+](https://img.shields.io/pypi/pyversions/tokenpak.svg)](https://pypi.org/project/tokenpak/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-green.svg)](LICENSE)

---

## 30-second demo

```bash
python -m pip install tokenpak
tokenpak demo
```

```
┌──────────────────────────────────────────────────────┐
│  TokenPak — Offline Fixture Demo                     │
├──────────────────────────────────────────────────────┤
│  Scenario              DevOps agent (config + logs)  │
│  Data source                built-in sample fixture  │
│  Savings drivers                      dedup + alias  │
├──────────────────────────────────────────────────────┤
│  Original                                747 tokens  │
│  Compressed                              502 tokens  │
│  Fixture delta                  245 tokens  (32.8%)  │
│  Fixture cost delta            $0.00073 per fixture  │
│  Receipt status               not a savings receipt  │
├──────────────────────────────────────────────────────┤
│  Stages: dedup, alias, segmentize, directives        │
└──────────────────────────────────────────────────────┘
```

Then verify:

```bash
tokenpak --version
```

```text
tokenpak 1.17.0
```

The offline fixture is illustrative, not a measured savings receipt. Token
counts vary by route and workload. For the signposted measured path, run a
supported authenticated client through the proxy and follow the
[first-receipt guide](docs/first-receipt.md).

---

## Works with

**Claude Code** · **Cursor** · **Cline** · **Continue.dev** · **Aider** ·
**OpenAI SDK** · **Anthropic SDK** · **LiteLLM** · **Codex**

Run `tokenpak integrate` to see supported-client setup guidance.

---

## Install

```bash
python -m pip install tokenpak
```

Requires Python 3.10+. See the [install guide](docs/install-guide.md) for
isolated-install options and [quickstart](docs/quickstart.md) for client setup.

---

## What's included

- **Context packing** — reduces repeated eligible context before provider send;
  inspect measured results with `tokenpak savings`.
- **Local proxy and client integration** — route supported clients through one
  local service without changing application code.
- **Spend Guard** — a pre-send circuit breaker that can block configured
  runaway requests before the provider call.
- **Local cost and receipt records** — inspect measured activity by model,
  session, and route.
- **Vault indexing and semantic search** — index a codebase and search it
  locally.
- **Compression recipes** — 50 configurable YAML recipes for supported flows.

---

## How it works

Your AI client sends provider-compatible requests to the TokenPak proxy on
`127.0.0.1`. TokenPak compresses eligible context and evaluates configured Spend
Guard limits before forwarding to the selected upstream provider, then records
the request result locally.

```text
AI client -> TokenPak proxy -> upstream provider
                   |
                   +-> local receipt and cost records
```

---

## Documentation

- [Quickstart](docs/quickstart.md)
- [API reference](docs/api-tpk-v1.md)
- [Architecture](docs/architecture.md)
- [First measured receipt](docs/first-receipt.md)
- [Spend Guard](docs/spend-guard.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Runnable examples](examples/README.md)

---

## Runnable examples

The repository examples are not bundled inside the PyPI wheel. Read the
[examples/README.md](examples/README.md), or clone the source and run the local,
credential-free compression example:

```bash
git clone https://github.com/tokenpak/tokenpak.git
cd tokenpak
python -m pip install -U tokenpak
python examples/basic_compression.py
```

---

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

The TokenPak open-source core is Apache-2.0 licensed. TokenPak Pro and hosted
services are proprietary. See [LICENSE](LICENSE) and [SECURITY.md](SECURITY.md).
