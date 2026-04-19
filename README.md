# TokenPak — Cut your LLM token spend by 30–50%, zero config

[![PyPI version](https://img.shields.io/pypi/v/tokenpak.svg)](https://pypi.org/project/tokenpak/)
[![Python 3.10+](https://img.shields.io/pypi/pyversions/tokenpak.svg)](https://pypi.org/project/tokenpak/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
<!-- CI badge: pending initial workflow setup — add after .github/workflows/ lands -->

TokenPak is a local proxy that compresses your LLM context before it hits the API — fewer tokens, lower cost, same results. No code changes, no cloud, no credentials stored.

> **Status: early preview.** Core compression engine and proxy are in place. Per-client auto-integration (the `tokenpak integrate` command) is not yet shipped — configure your client manually by pointing it at `http://127.0.0.1:8766`. See [docs/QUICKSTART.md](docs/QUICKSTART.md).

---

## Quick start

```bash
pip install tokenpak
tokenpak start                      # start the local proxy at 127.0.0.1:8766
```

Point your LLM client at the proxy. For example, the Anthropic SDK:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8766
```

Or for OpenAI-compatible clients:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8766
```

Then use your client normally. TokenPak compresses requests on the way out and logs savings to a local SQLite ledger.

See [docs/QUICKSTART.md](docs/QUICKSTART.md) for per-client setup (Claude Code, Cursor, Aider, and others).

---

## What savings look like

After a few proxied requests, `tokenpak savings` reports the cumulative reduction:

```
┌──────────────────────────────────────────────────────┐
│  TokenPak — Savings                                  │
├──────────────────────────────────────────────────────┤
│  Sample scenario       DevOps agent (config + logs)  │
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

Actual numbers depend on your workload. Agent-style prompts with lots of repeated context see the biggest gains.

---

## Works with

Any LLM client that respects a custom base URL:

**Claude Code** · **Cursor** · **Cline** · **Continue.dev** · **Aider** · **OpenAI SDK** · **Anthropic SDK** · **LiteLLM** · **Codex**

Per-client configuration steps are in [docs/QUICKSTART.md](docs/QUICKSTART.md). Auto-wiring via a single `tokenpak integrate <client>` command is tracked for a future release.

---

## Install

```bash
pip install tokenpak
```

TokenPak's runtime dependencies include `anthropic`, `openai`, `fastapi`, `flask`, `litellm`, `llmlingua`, `pandas`, `pydantic`, `requests`, `rich`, `scipy`, `sentence-transformers`, `tree-sitter-languages`, `watchdog`, and a few others — all installed automatically. Note that `sentence-transformers` and `scipy` are large (several hundred MB of dependencies); expect `pip install` to take a few minutes on first install.

Requires Python 3.10+.

See [docs/QUICKSTART.md](docs/QUICKSTART.md) for virtual-env setup and first-run details.

---

## What's included

- **Context compression** — deterministic pipeline (dedup → alias → segmentize → directives); typical 30–50% token reduction on agent workloads.
- **Local proxy** — runs at `127.0.0.1:8766`; zero cloud component.
- **Model routing** — configurable rules with fallback chains.
- **Cost & savings tracking** — per model, per session, per agent; local SQLite (`~/.tokenpak/monitor.db`).
- **Dashboard** — local web UI for visualizing savings (`tokenpak dashboard`).
- **Vault indexing + semantic search** — index a directory; search without an LLM call.
- **A/B testing and request replay** — compare compression configs; re-run past requests.
- **50 built-in compression recipes** — YAML, customizable.

See [docs/QUICKSTART.md](docs/QUICKSTART.md) and [docs/API.md](docs/API.md) to get started.

---

## Current limitations

Honest about what isn't ready yet:

- **No `tokenpak integrate <client>` auto-wire command** — configure clients by env var as shown above. Auto-wire is planned.
- **No published CI/CD** — releases are manual; automation is tracked in the release-workflow standards.
- **`tokenpak demo` is a compression-recipes demo** (shows recipes applied to a sample input), not the decorated savings panel above. The panel shows what `tokenpak savings` output can look like after real usage.

We'd rather ship an honest preview than an advertised product that doesn't match install-time reality.

---

## Support

- **Docs:** [docs/QUICKSTART.md](docs/QUICKSTART.md) · [docs/API.md](docs/API.md) · [docs/FAQ.md](docs/FAQ.md)
- **Issues:** [github.com/tokenpak/tokenpak/issues](https://github.com/tokenpak/tokenpak/issues)
- **Discussions:** [github.com/tokenpak/tokenpak/discussions](https://github.com/tokenpak/tokenpak/discussions)
- **Email:** hello@tokenpak.ai

---

## License

Apache 2.0. See [LICENSE](LICENSE).
