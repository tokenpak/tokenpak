# TokenPak — Cut your LLM token spend by 30–50%. One command to configure your LLM proxy.

[![PyPI version](https://img.shields.io/pypi/v/tokenpak.svg)](https://pypi.org/project/tokenpak/)
[![Python 3.10+](https://img.shields.io/pypi/pyversions/tokenpak.svg)](https://pypi.org/project/tokenpak/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
<!-- CI badge: pending initial workflow setup — add after .github/workflows/ lands -->

TokenPak is a local proxy that compresses your LLM context before it hits the API — fewer tokens, lower cost, same results. No code changes, no cloud, no credentials stored.

> **Status: early preview.** Core compression engine and proxy are in place. `tokenpak setup` is the interactive wizard that detects your API keys, picks a compression profile, and starts the proxy. Per-client auto-integration (the forthcoming `tokenpak integrate` command) is not yet shipped — after `tokenpak setup` runs, point your client at `http://127.0.0.1:8766` via the one-line `export` below. See QUICKSTART at https://github.com/tokenpak/docs (rendered at tokenpak.ai/quickstart).

---

## Quick start

```bash
pip install tokenpak
tokenpak setup                      # interactive wizard — detects keys, picks a profile, starts the proxy
```

Then point your LLM client at the proxy with one env var. For the Anthropic SDK:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8766
```

Or for OpenAI-compatible clients:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8766
```

Then use your client normally. TokenPak compresses requests on the way out and logs savings to a local SQLite ledger.

If you prefer manual configuration (no wizard), `tokenpak start` brings the proxy up with defaults and you set `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL` yourself.

Reproduce the 30–50% headline claim locally: `make benchmark-headline`.

See QUICKSTART at https://github.com/tokenpak/docs (rendered at tokenpak.ai/quickstart) for per-client setup (Claude Code, Cursor, Aider, and others).

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

Per-client configuration steps are in QUICKSTART at https://github.com/tokenpak/docs (rendered at tokenpak.ai/quickstart). Auto-wiring via a single `tokenpak integrate <client>` command is tracked for a future release.

---

## Install

```bash
pip install tokenpak
```

TokenPak's runtime dependencies include `anthropic`, `openai`, `fastapi`, `flask`, `litellm`, `llmlingua`, `pandas`, `pydantic`, `requests`, `rich`, `scipy`, `sentence-transformers`, `tree-sitter-languages`, `watchdog`, and a few others — all installed automatically. Note that `sentence-transformers` and `scipy` are large (several hundred MB of dependencies); expect `pip install` to take a few minutes on first install.

Requires Python 3.10+.

See QUICKSTART at https://github.com/tokenpak/docs (rendered at tokenpak.ai/quickstart) for virtual-env setup and first-run details.

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

See QUICKSTART at https://github.com/tokenpak/docs (rendered at tokenpak.ai/quickstart) and API reference at https://github.com/tokenpak/docs (rendered at tokenpak.ai/api) to get started.

---

## Current limitations

Honest about what isn't ready yet:

- **No `tokenpak integrate <client>` auto-wire command** — configure clients by env var as shown above. Auto-wire is planned.
- **No published CI/CD** — releases are manual; automation is tracked in the release-workflow standards.
- **`tokenpak demo` is a compression-recipes demo** (shows recipes applied to a sample input), not the decorated savings panel above. The panel shows what `tokenpak savings` output can look like after real usage.

We'd rather ship an honest preview than an advertised product that doesn't match install-time reality.

---

## Support

- **Docs:** QUICKSTART at https://github.com/tokenpak/docs (rendered at tokenpak.ai/quickstart) · API reference at https://github.com/tokenpak/docs (rendered at tokenpak.ai/api) · FAQ at https://github.com/tokenpak/docs (rendered at tokenpak.ai/faq)
- **Issues:** [github.com/tokenpak/tokenpak/issues](https://github.com/tokenpak/tokenpak/issues)
- **Discussions:** [github.com/tokenpak/tokenpak/discussions](https://github.com/tokenpak/tokenpak/discussions)
- **Email:** hello@tokenpak.ai

---

## License

Apache 2.0. See [LICENSE](LICENSE).
