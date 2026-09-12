# TokenPak — Local LLM proxy — guided setup

[![PyPI version](https://img.shields.io/pypi/v/tokenpak.svg)](https://pypi.org/project/tokenpak/)
[![Python 3.10+](https://img.shields.io/pypi/pyversions/tokenpak.svg)](https://pypi.org/project/tokenpak/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-green.svg)](LICENSE)
<!-- CI badge: pending repo transfer to tokenpak/tokenpak — add after transfer is confirmed -->

> **The open logistics layer for AI context.**

TokenPak runs as a **local LLM proxy with request records and explicit context tools**. The default proxy preserves conversation turns; a forwarded request can correctly report zero tokens saved. Explicit compression operations can reduce eligible content. Provider-bound requests still go to your chosen provider, with no TokenPak cloud relay.

---

## First measured request receipt in three commands

Prerequisites: Python 3.10+ and an already authenticated supported client. The
reference path below uses Codex and reuses its existing OAuth login and normal
default model. An API key or explicit model override is optional, not required.
Run it from a real project; provider usage may count against your subscription
or incur provider charges.

```bash
python -m pip install tokenpak
tokenpak serve --profile aggressive --stats-footer  # terminal 1; leave running
tokenpak codex  # terminal 2; use your existing login and selected/default model
```

In Codex, make a normal context-bearing request. The proxy prints a measured
receipt in terminal 1. In this unmodified reference setup, the built-in Pak
builder leaves every system, user, and assistant conversation turn intact, so
the receipt truthfully reports `0 tokens saved`. This verifies routing and
accounting without claiming savings that did not occur. The session-only
footer is off by default and does not alter the provider response.

See the [first receipt guide](docs/first-receipt.md) for prerequisites,
the expected zero-savings output, the five-minute reference target, and the
separate explicit compression surfaces.

## Offline fixture demo

To inspect compression without credentials or provider spend:

```bash
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

> This is an illustrative fixture, not a measured first-request receipt. Token
> counts vary by route and workload. Measure your own with `tokenpak savings`;
> inspect provider-cache vs. TokenPak attribution with
> `tokenpak status --tip-cache`.

---

## Works with

- **Tested SDK adapters:** OpenAI SDK, Anthropic SDK and LiteLLM.
- **First-class integrations:** Claude Code and Codex.
- **Compatibility targets, not yet independently verified:** Cursor, Cline,
  Continue.dev and Aider.

Run `tokenpak integrate` to see the full client list with setup guides for each.

---

## Install

```bash
uv tool install tokenpak      # or: pipx install tokenpak
```

Either one installs the `tokenpak` command into its own isolated environment.
To use TokenPak as a library inside a project, `pip install tokenpak` into an
activated virtual environment instead.

See [docs/install-guide.md](docs/install-guide.md) for per-installer detail,
optional extras, and the `externally-managed-environment` (PEP 668) error.
See [docs/quickstart.md](docs/quickstart.md) for per-client configuration.

Requirements: Python 3.10+.

Exposing the proxy beyond `127.0.0.1`? Set `TOKENPAK_PROXY_AUTH_TOKEN` to a
shared secret to require `Authorization: Bearer <token>` on remote requests
(see [docs/configuration/proxy-auth.md](docs/configuration/proxy-auth.md)).

---

## Runnable examples

The PyPI wheel keeps the install slim and does not bundle the repository's
top-level examples. To run them after a normal package install, clone or
download the source tree for the example files:

```bash
git clone https://github.com/tokenpak/tokenpak.git
cd tokenpak
python -m venv .venv
source .venv/bin/activate
python -m pip install -U tokenpak
python examples/basic_compression.py
```

`examples/basic_compression.py` is local-first and does not require provider
credentials. See [examples/README.md](examples/README.md) for the full examples
index and developer editable-install path.

---

## What's included (Free)

> **Dispatch (v0.1-alpha preview):** a scoped, resumable, reviewable workflow-control surface. Released packages include the CLI and runtime modules; runtime commands require the optional `[dispatch]` dependencies. Live station execution and delivery receipts are not wired yet. See the [Dispatch guide](docs/guides/dispatch.md).

- **Context tools and truthful receipts** — explicit compression operations can
  reduce eligible content. The built-in Pak builder preserves role-bearing
  conversation turns; byte-preserved routes report zero product-attributed
  reduction. Inspect recorded usage with `tokenpak savings` and cache attribution
  with `tokenpak status --tip-cache`. `make benchmark-headline` exercises a fixed
  fixture; its result is not a default-proxy savings receipt.
- **Client integration** — setup guides and helpers for the compatibility tiers above
- **Routing policy** — configuration and observe-mode records; automatic model
  changes and fallback enforcement are not active by default
- **Cost tracking** — per model, per session, per agent; local SQLite, zero cloud
- **TIP Spend Guard** — pre-send circuit breaker; blocks runaway requests before provider call. Yes/No release or `[TIP: allow=once max=$X]` directive. Catches both single-request spikes and the death-by-1000-cuts pattern via session-cumulative tracking. See [docs/spend-guard.md](docs/spend-guard.md).
- **Vault indexing + semantic search** — index your codebase; search without an LLM call
- **MultiPak Pro Phase 1 OSS surface** — read-only Vault Pak adapter, companion journal promotion-candidate marking, `tokenpak pak` CLI, `/pak/v1/*` proxy stubs. Full MultiPak (capture pipeline, recall ranking, Handoff Paks, anchor hydration) requires `tokenpak-paid` (Pro). See [docs/multipak.md](docs/multipak.md).
- **CLI + proxy server** — `tokenpak serve`, `tokenpak cost`, `tokenpak savings`
- **Value proof** — `tokenpak prove run` benchmarks direct API vs. TokenPak on your own prompt workload and prints a side-by-side cost/token report. See the [value proof guide](docs/guides/value-proof.md).
- **A/B testing and replay/debug** — compare compression configs, replay past requests
- **50 built-in compression recipes** — YAML, customizable

Provider cache reuse is distinct from TokenPak context reduction. A provider cache hit does not mean the proxy omitted that context from the request. See [docs/quickstart.md](docs/quickstart.md) and [docs/api-tpk-v1.md](docs/api-tpk-v1.md) to get started.

---

## Open source & editions

TokenPak's core is Apache-2.0 open source. TokenPak Pro is the proprietary
`tokenpak-paid` package, distributed separately with license requirements.
Hosted services remain deferred.

---

## Support

- **Docs:** [docs/quickstart.md](docs/quickstart.md) · [API reference](docs/api-tpk-v1.md) · [examples/README.md](examples/README.md)
- **Issues:** [github.com/tokenpak/tokenpak/issues](https://github.com/tokenpak/tokenpak/issues)
- **Discussions:** [github.com/tokenpak/tokenpak/discussions](https://github.com/tokenpak/tokenpak/discussions)
- **Email:** hello@tokenpak.ai

---

## License

The TokenPak open-source core is licensed under the Apache License 2.0 — see [LICENSE](LICENSE). TokenPak Pro is proprietary; hosted services remain deferred.

### Trademark

"TokenPak", the TokenPak name, logo, and brand assets are trademarks of TokenPak and are **not** licensed under Apache-2.0 (Apache-2.0 §6 grants no trademark rights). Nominative and reference use — for example "works with TokenPak" or "a plugin for TokenPak" — is fine. Using the name or logo in a way that implies endorsement, sponsorship, or affiliation, or naming a fork, product, or service "TokenPak" (or something confusingly similar), is not.
