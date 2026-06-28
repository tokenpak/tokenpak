# TokenPak — Cut your LLM token spend — zero config

[![PyPI version](https://img.shields.io/pypi/v/tokenpak.svg)](https://pypi.org/project/tokenpak/)
[![Python 3.10+](https://img.shields.io/pypi/pyversions/tokenpak.svg)](https://pypi.org/project/tokenpak/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-green.svg)](LICENSE)
<!-- CI badge: pending repo transfer to tokenpak/tokenpak — add after transfer is confirmed -->

> **The open logistics layer for AI context.**

TokenPak starts as a local proxy that **packs AI requests** before they ship — reducing wasted context and giving teams receipts for what changed. Fewer tokens, lower cost. No code changes, no cloud, no credentials stored.

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
│  TokenPak — Live Compression Demo (illustrative)     │
├──────────────────────────────────────────────────────┤
│  Scenario              DevOps agent (config + logs)  │
│  Savings drivers                      dedup + alias  │
├──────────────────────────────────────────────────────┤
│  Original                                747 tokens  │
│  Compressed                              502 tokens  │
│  Fewer tokens                            245 tokens  │
├──────────────────────────────────────────────────────┤
│  Stages: dedup, alias, segmentize, directives        │
└──────────────────────────────────────────────────────┘
```

> Illustrative fixture — token counts vary by route and workload. Measure your
> own with `tokenpak savings`; inspect provider-cache vs. TokenPak attribution
> with `tokenpak status --tip-cache`.

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

Exposing the proxy beyond `127.0.0.1`? Set `TOKENPAK_PROXY_AUTH_TOKEN` to a
shared secret to require `Authorization: Bearer <token>` on remote requests
(see [docs/configuration/proxy-auth.md](docs/configuration/proxy-auth.md)).

---

## What's included (Free)

> **Dispatch (v0.1-alpha preview):** turn a request into a scoped, resumable, reviewable workflow from the CLI. It is a source/`main`-branch preview and is not yet part of a released `pip install tokenpak`; see the [Dispatch guide](docs/guides/dispatch.md).

- **Context compression** — deterministic token reduction on real agent
  workloads, <50ms latency. Savings are route-specific: direct API, CLI, and
  uncached repeated-agent loops are the best fit, while Claude Code/TUI routes
  may show lower incremental savings when the provider cache already handled
  repeated context. Measure your own savings with `tokenpak savings`; inspect
  attribution with `tokenpak status --tip-cache` (reproduce the headline
  benchmark with `make benchmark-headline`).
- **Client integration** — one command wires Claude Code, Cursor, Aider, and 6 other clients
- **Model routing** — send requests to the right model automatically, with fallback rules
- **Cost tracking** — per model, per session, per agent; local SQLite, zero cloud
- **TIP Spend Guard** — pre-send circuit breaker; blocks runaway requests before provider call. Yes/No release or `[TIP: allow=once max=$X]` directive. Catches both single-request spikes and the death-by-1000-cuts pattern via session-cumulative tracking. See [docs/spend-guard.md](docs/spend-guard.md).
- **Vault indexing + semantic search** — index your codebase; search without an LLM call
- **MultiPak Pro Phase 1 OSS surface** — read-only Vault Pak adapter, companion journal promotion-candidate marking, `tokenpak pak` CLI, `/pak/v1/*` proxy stubs. Full MultiPak (capture pipeline, recall ranking, Handoff Paks, anchor hydration) requires `tokenpak-paid` (Pro). See [docs/multipak.md](docs/multipak.md).
- **CLI + proxy server** — `tokenpak serve`, `tokenpak cost`, `tokenpak savings`
- **A/B testing and replay/debug** — compare compression configs, replay past requests
- **50 built-in compression recipes** — YAML, customizable

Repeated context is reused from cache instead of re-sent on every call. See [docs/quickstart.md](docs/quickstart.md) and [docs/api-tpk-v1.md](docs/api-tpk-v1.md) to get started.

---

## Open source & editions

TokenPak's core is Apache-2.0 open source; TokenPak Pro and hosted services are proprietary. Commercial packaging is not published yet.

---

## Support

- **Docs:** [docs/quickstart.md](docs/quickstart.md) · [API reference](docs/api-tpk-v1.md)
- **Issues:** [github.com/tokenpak/tokenpak/issues](https://github.com/tokenpak/tokenpak/issues)
- **Discussions:** [github.com/tokenpak/tokenpak/discussions](https://github.com/tokenpak/tokenpak/discussions)
- **Email:** hello@tokenpak.ai

---

## License

The TokenPak open-source core is licensed under the Apache License 2.0 — see [LICENSE](LICENSE). TokenPak Pro and hosted services are proprietary.

### Trademark

"TokenPak", the TokenPak name, logo, and brand assets are trademarks of TokenPak and are **not** licensed under Apache-2.0 (Apache-2.0 §6 grants no trademark rights). Nominative and reference use — for example "works with TokenPak" or "a plugin for TokenPak" — is fine. Using the name or logo in a way that implies endorsement, sponsorship, or affiliation, or naming a fork, product, or service "TokenPak" (or something confusingly similar), is not.
