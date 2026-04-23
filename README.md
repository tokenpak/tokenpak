# TokenPak — Up to 90%+ savings on direct API/CLI and other favorable uncached workloads. One command to configure your LLM proxy.

[![PyPI version](https://img.shields.io/pypi/v/tokenpak.svg)](https://pypi.org/project/tokenpak/)
[![Python 3.10+](https://img.shields.io/pypi/pyversions/tokenpak.svg)](https://pypi.org/project/tokenpak/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
<!-- CI badge: pending initial workflow setup — add after .github/workflows/ lands -->

TokenPak is a local proxy that compresses your LLM context before it hits the API — fewer tokens, lower cost, same results. No code changes, no cloud, no credentials stored.

**Observed savings vary by integration path and provider-side cache behavior.** On direct API / CLI / uncached workloads the deterministic pipeline routinely lands in the 90%+ band. On provider-cached flows (Claude Code and similar) observed incremental savings can be much lower, because the provider's own cache already absorbs most of the token pool and TokenPak only optimizes the user-controlled portion. See [How we report savings](#how-we-report-savings) for the full framing.

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

Reproduce the savings floor locally: `make benchmark-headline` (asserts ≥30% reduction on a pinned agent-style fixture; the CI-enforced floor, not the ceiling).

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

- **Context compression** — deterministic pipeline (dedup → alias → segmentize → directives). Up to 90%+ reduction on direct-API / uncached workloads; lower on provider-cached flows. CI-enforced ≥30% floor on the pinned agent-style fixture.
- **Local proxy** — runs at `127.0.0.1:8766`; zero cloud component.
- **Model routing** — configurable rules with fallback chains. Route-class policy presets ship under `tokenpak/services/policy_service/presets/` covering Claude Code (TUI/CLI/TMUX/SDK/IDE/CRON), Anthropic SDK, OpenAI SDK, and generic.
- **Cost & savings tracking** — per model, per session, per agent; local SQLite (`~/.tokenpak/monitor.db`).
- **Dashboard** — local web UI for visualizing savings at `http://127.0.0.1:8766/dashboard` (also reachable via `tokenpak dashboard`).
- **Vault indexing + semantic search** — index a directory; search without an LLM call.
- **A/B testing and request replay** — compare compression configs; re-run past requests.
- **Custom compression recipes** — author your own via `tokenpak recipe create/validate/test/benchmark`.

## How we report savings

TokenPak's savings aren't a single number because they shouldn't be. The real savings depend on where in your stack the compression runs:

- **Direct API calls, CLI tools, SDK integrations, and any uncached workload** — the compression pipeline operates on the full token pool. Observed savings routinely reach **90%+** on realistic agent-style prompts. The pinned CI benchmark measures this path; `make benchmark-headline` reproduces it.
- **Provider-cached flows (Claude Code and similar integrations)** — your client uses the provider's server-side prompt cache for most of the prompt (system prompt, tool definitions, historical turns). TokenPak only optimizes the **user-controlled portion** of the token pool. Observed incremental savings on these paths can be much lower — sometimes a few percent of total spend — because the provider cache already did the heavy lifting. This isn't TokenPak failing; it's an honest division of labor.

If you're evaluating TokenPak, start with a direct-API workload to see the compression pipeline's actual effectiveness, then layer in your cached flows to see the marginal contribution on top of the provider cache.

See QUICKSTART at https://github.com/tokenpak/docs (rendered at tokenpak.ai/quickstart) and API reference at https://github.com/tokenpak/docs (rendered at tokenpak.ai/api) to get started.

---

## Pro tier

**Pro** adds team-scale features on top of the OSS core. The OSS proxy, compression engine, client integrations, local dashboard, telemetry store, and route-class policy presets all stay in place — Pro layers the features a team running TokenPak at scale needs:

- **Team-scale cost attribution** — shared dashboard with multi-seat cost attribution: which workloads, which engineers, which tools drive spend, without wiring a BI pipeline.
- **Budget enforcement** — monthly budget caps map to hard `429 budget_exceeded` responses at request time. Not a passive dashboard: the proxy refuses to forward the request instead of silently burning through the rest of your budget.
- **Advanced routing policies** — cost-aware fallbacks, SLA-aware failover across providers, tiered routing by workload kind. OSS gives you the rule surface; Pro gives you the policy tooling that runs on top.
- **Enterprise credential management** — credential-rotation hooks, audit-log export, compliance rulesets that plug into the compression pipeline.
- **SSO, priority support, SLA** — dashboard SSO, priority support with an SLA, team onboarding.

Ships as the `tokenpak-paid` package via a private license-gated index at `pypi.tokenpak.ai`. See [tokenpak.ai/paid](https://tokenpak.ai/paid) to request access.

Installing the Pro package (after you have a license key):

```bash
pip install --index-url https://pypi.tokenpak.ai --extra-index-url https://pypi.org/simple tokenpak-paid
tokenpak activate <your-license-key>
```

Running `pip install tokenpak-paid-stub` from public PyPI fetches a discovery stub that prints these install instructions — so `pip` works as a learning path, not a dead end. The real paid code stays license-gated.

---

## Privacy + compliance

TokenPak is local-first by design. Every prompt, response, and API key stays on your machine. The only data that ever leaves your machine is the LLM request you were going to send anyway — forwarded to your configured provider using your own credentials.

- [**Privacy**](https://tokenpak.ai/compliance/privacy) — what's stored locally, what leaves, and every optional debug-logging escape hatch disclosed in full.
- [**Data Processing Agreement (template)**](https://tokenpak.ai/compliance/dpa) — IAPP-based template; marked pending legal review.
- [**Sub-processors**](https://tokenpak.ai/compliance/sub-processors) — Stripe / Cloudflare / Fly.io / GitHub / PyPI for the Pro-tier infrastructure; the OSS proxy has none.

For the runtime posture verification, run `tokenpak doctor --privacy` (plain-English summary) or `tokenpak doctor --conformance` (executes the TIP-1.0 self-conformance suite).

---

## Current limitations

Honest about what isn't ready yet:

- **No `tokenpak integrate <client>` auto-wire command** — configure clients by env var as shown above. Auto-wire is planned.
- **No published CI/CD** — releases are manual; automation is tracked in the release-workflow standards.
- **`tokenpak demo` is a compression-recipes demo** (shows recipes applied to a sample input), not the decorated savings panel above. The panel shows what `tokenpak savings` output can look like after real usage.

We'd rather ship an honest preview than an advertised product that doesn't match install-time reality.

---

## Non-localhost access

TokenPak's default is localhost-only. If you want to expose the proxy to other machines on your LAN, set an auth token:

```bash
export TOKENPAK_PROXY_AUTH_TOKEN=$(openssl rand -hex 32)
tokenpak start                # or tokenpak setup for first-time config
```

Clients then include the token on non-localhost requests:

```
X-TokenPak-Auth: <your-token>
```

Localhost (`127.0.0.1`, `::1`) traffic bypasses auth — your local tools keep working without changes. Non-localhost requests without the env var return `403 forbidden`; requests with a missing or wrong header return `401 unauthorized`. The token is stripped from the request before any upstream forward, so provider APIs (Anthropic, OpenAI, etc.) never see it.

---

## Support

- **Docs:** QUICKSTART at https://github.com/tokenpak/docs (rendered at tokenpak.ai/quickstart) · API reference at https://github.com/tokenpak/docs (rendered at tokenpak.ai/api) · FAQ at https://github.com/tokenpak/docs (rendered at tokenpak.ai/faq)
- **Issues:** [github.com/tokenpak/tokenpak/issues](https://github.com/tokenpak/tokenpak/issues)
- **Discussions:** [github.com/tokenpak/tokenpak/discussions](https://github.com/tokenpak/tokenpak/discussions)
- **Email:** hello@tokenpak.ai

---

## License

Apache 2.0. See [LICENSE](LICENSE).
