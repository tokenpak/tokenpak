# Onboarding — TokenPak

A month's worth of first contact, split into five short sessions. Each step is optional; you can stop any time and the proxy keeps working.

---

## Day 1 — First request, first savings

```bash
pip install tokenpak
tokenpak setup          # detects your API keys, picks a profile, starts the proxy
```

Then point your LLM client at the proxy:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8766     # Anthropic SDK or Claude Code
# or
export OPENAI_BASE_URL=http://127.0.0.1:8766        # OpenAI-compatible clients
```

Issue a normal request with your client. Compression runs automatically on prompts above the default threshold (4,500 tokens). You should see your usual response — just faster to produce and cheaper to pay for.

Check that the proxy is actually intercepting traffic:

```bash
tokenpak status         # recent requests, cache-read rate, latency
```

**What to expect**: 30–50% input-token reduction on agent-style workloads (README headline claim, pinned by CI). You should see the reduction reflected in `tokenpak cost --week` within a few requests.

---

## Day 3 — Confirm the savings are real

```bash
tokenpak cost --week            # $ spent, broken down by model
tokenpak savings                # total tokens compressed vs. uncompressed
tokenpak attribution            # per-agent / per-session breakdown
```

If `savings` shows zero or very low values after a couple of days, it usually means one of:
- Your prompts are below the compaction threshold (tune `TOKENPAK_COMPACT_THRESHOLD_TOKENS`).
- The client bypasses the proxy (check `tokenpak status` — if request counts are zero, your client isn't hitting the proxy).
- A regression on the headline benchmark — rare; reproduce locally with `make benchmark-headline`.

---

## Day 7 — Customize a compression recipe

TokenPak ships 50 OSS recipes covering common agent workloads (code review, RAG, tool-heavy prompts, long-context summarization, etc.). You can inspect and add to them.

```bash
tokenpak demo --list            # browse built-in recipes
tokenpak recipe list            # names only
tokenpak recipe show <name>     # details + operations
```

Dropping a custom recipe is a YAML file in `~/.tokenpak/recipes/`; `tokenpak demo <name>` will apply it. Recipes are deterministic, so outputs are reproducible across runs and across teammates.

---

## Day 14 — Set a budget and configure alerts

Budget enforcement is a **Pro** feature (part of `tokenpak-paid`, the commercial tier). It turns monthly spend caps into hard `429 budget_exceeded` responses — the proxy refuses to forward the request instead of silently burning through the rest of your budget.

```bash
tokenpak plan                    # see your current tier
tokenpak activate <license-key>  # activate a Pro license
```

You can start a Pro trial at [`https://tokenpak.ai/paid`](https://tokenpak.ai/paid). With Pro active, `tokenpak budget set --monthly 100` and the alerting pipeline (webhook + Slack channels) become available.

---

## Day 30 — Deploy to production

By now the proxy has a month of behavior data on real traffic. A few operational tips for moving it into production:

- **Run it as a systemd service.** Example unit file: `examples/systemd/tokenpak.service`. Start on boot; restart on failure.
- **Expose to other machines (LAN / containerized clients)** by setting `TOKENPAK_PROXY_AUTH_TOKEN=$(openssl rand -hex 32)` in the proxy environment and sending `X-TokenPak-Auth: <token>` from non-localhost clients. Localhost clients are unaffected. See [Non-localhost access](../README.md#non-localhost-access) in the README.
- **Monitor proactively.** The Prometheus endpoint at `/metrics` exports request count, latency, compression rates, and cache-origin attribution — scrape it into your existing observability stack.
- **Rotate license keys** via `tokenpak deactivate` then `tokenpak activate <new-key>`. Deactivation is always safe; the proxy falls back to OSS tier on any license-validation error (never fail-closed).
- **Audit compliance surface.** See [Privacy](https://tokenpak.ai/compliance/privacy), [DPA](https://tokenpak.ai/compliance/dpa), and [Sub-processors](https://tokenpak.ai/compliance/sub-processors) for the canonical data-flow map.

---

## Running into trouble?

- `tokenpak doctor` runs an end-to-end diagnostic and prints anything that looks off.
- `tokenpak doctor --conformance` validates TIP-1.0 self-conformance against the shipped registry schemas.
- Questions: [hello@tokenpak.ai](mailto:hello@tokenpak.ai).
- Issues: [github.com/tokenpak/tokenpak/issues](https://github.com/tokenpak/tokenpak/issues).
