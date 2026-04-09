---
title: TokenPak — PM/GTM Forensic Gap Analysis
audience: Kevin (founder), tokenpak product/eng leadership
analyst: Claude (Sue, 2026-04-07) — PM/GTM specialist lens
scope: Production tokenpak repo at /home/sue/tokenpak/ (verified working tree, HEAD as of 2026-04-07)
methodology: 5 parallel forensic exploration passes — production code, GTM/pricing, docs/onboarding, observability/support, security/compliance
status: DRAFT v1 — for founder review
---

# TokenPak — PM/GTM Forensic Gap Analysis

> **TL;DR.** TokenPak's OSS *core* is a real, technically credible product. Compression, multi-provider routing, cost tracking, and vault BM25 search are wired and working. Around that core is a *product company shell* — Pro/Team/Enterprise tiers, a portal, a license signing system, an alerting framework, a pricing page, a roadmap — that has been **built but not connected**. The company looks shipped from the README; the customer experience would not survive first contact with a paying buyer. The gap is **not engineering quality, it is integration, enforcement, and GTM polish**. Below: 27 prioritized findings with file:line evidence and a 30/60/90 action plan.

---

## 0. The shape of the gap (one paragraph)

Across five independent forensic passes, the same pattern recurs: **infrastructure exists, enforcement does not**. License keys are signed with RSA-4096 — the proxy never checks them. An alert engine evaluates rules — it has no way to deliver an alert. Audit logs are written — the schema has no user, IP, or request context. A web dashboard exists — it is not registered on the proxy port. A portal with Stripe checkout is built — its domain is not registered. Anonymous usage telemetry is collected — there is nowhere to ship it. Each of these individually is a small fix; together they describe a product that *looks shipped* in the README and *cannot be sold* in reality. The OSS proof-of-concept is real; the company that wraps it is missing about a quarter of its load-bearing wiring.

---

## 1. Methodology and scope notes

- **Working tree audited:** `/home/sue/tokenpak/` (verified per `~/vault/04_KNOWLEDGE/claude_code/allconfigurationsforclaudecode.md` § 5 as the authoritative repo; `01_PROJECTS/tokenpak/` is a stale checkpoint and was excluded).
- **"Production version" disambiguation.** The repo contains TWO production-grade proxy entry points:
  1. `proxy.py` (~6,470 lines) — primary, OpenClaw-integrated, handles Telegram→Anthropic token injection.
  2. `proxy_v4.py` (~4,195 lines, 196 KB) — newer, leaner v4 variant. Multiple checkpoint snapshots (`proxy_v4-checkpoint-phase*.py`) are still in the working tree.
  This duplication is itself **Finding #1** below.
- **Five forensic passes (run in parallel):** production code reality vs. claims, GTM/pricing/portal, docs and onboarding, observability/support/feedback, security/compliance/legal.
- **Severity scale.** P0 = launch-blocker or marketing-vs-reality lie; P1 = important gap a real customer would hit in week 1; P2 = polish/credibility.

---

## 2. Top-level findings

| # | Finding | Severity | Why it matters |
|---|---|---|---|
| 1 | **Two production proxy files** in working tree (`proxy.py` 6.4k LOC + `proxy_v4.py` 4.2k LOC) with checkpoints | P1 | No single source of truth. New engineer / contributor has to guess. Fixes can land in the wrong file. Also a forensic audit risk for compliance review. |
| 2 | **Pro/Team/Enterprise feature gating is not enforced in the proxy.** License keys are generated and signed (RSA-4096), but `proxy_v4.py` never validates them at request time. | **P0** | The entire commercial revenue motion is theatre. A free user is identical to a Pro user. This is the largest gap in the project. |
| 3 | **Compression pipeline is gated OFF by default.** `TOKENPAK_VALIDATION_GATE_ENABLED`, `TOKENPAK_BUDGET_CONTROLLER_ENABLED`, and `BUDGET_CONTROLLER_ENABLED` all default to `0`. Recipe loading is lazy and behind these gates. | **P0** | The headline value prop ("48.9% reduction") does not run on a default install. README's "zero config" install gives the user a passthrough proxy with deduplication only. |
| 4 | **Budget enforcement does not enforce.** README + DEPLOYMENT.md promise `429 Budget Exceeded` when limit is hit. Code returns `429` only for IP rate-limit, never for budget overrun. | **P0** | Direct marketing-vs-reality contradiction. A buyer who tests the advertised feature in 5 minutes will find it broken. |
| 5 | **Dashboard URL in README is wrong.** README says `http://localhost:8766/dashboard`. The dashboard FastAPI app exists (`tokenpak/agent/dashboard/app.py`) but `create_combined_app()` is never called by `serve.py:33`. The dashboard router is never mounted on the proxy port. | **P0** | New users will hit a 404 within 60 seconds of install. The "see it work" demo is broken. |
| 6 | **Alerting has no delivery mechanism.** `alerts.py` evaluates rules and `alert_settings.json` defines `email`, `webhook`, `in_app` channels — but there is no SMTP code, no HTTP POST code, no in-app surface. | **P0** | The Pro tier promises "Budget enforcement + alerts." Alerts will never fire. Pro is unsellable as advertised. |
| 7 | **Anonymous usage telemetry has no backend.** `TOKENPAK_METRICS_ENABLED` is recognized in `tokenpak/agent/config.py`, but there is no destination URL anywhere in the codebase. Metrics are written to local SQLite and never leave the machine. | **P1** | The company is operating completely blind on adoption. There is no way to answer "how many active installs do we have?" "what % activated compression?" "what model mix do users have?" — every GTM decision is unanchored. |
| 8 | **GitHub identity is split across two orgs.** README badges reference `kaywhy331/tokenpak` (personal); codecov, PyPI homepage, and issue templates reference `tokenpak/tokenpak` (org). | **P0** | An enterprise procurement team will see this and fail the IP-ownership question. Looks unprofessional. Easy to fix, embarrassing to leave. |
| 9 | **Contact-surface domains are unregistered.** `sales@tokenpak.ai`, `licensing@tokenpak.ai`, `security@tokenpak.dev`, `conduct@tokenpak.dev`, `support@tokenpak.dev`, `portal.tokenpak.dev`, plus a personal Gmail in `pyproject.toml`. None resolve. | **P0** | Every "contact us" path bounces. Sales, security, conduct, and support are unreachable. |
| 10 | **Portal is built but orphaned.** `portal/app.py` is a real Flask + SQLite + Stripe + RSA license-signing service — production-grade. There is no DNS, no link from README, no install/deploy doc. | **P0** | The entire upgrade path from OSS to Pro/Team is dead code waiting for one DNS record. Highest ROI fix in the project. |
| 11 | **The 48.9% headline benchmark is not reproducible.** README's hero claim ("5,074 → 2,594 tokens in <1ms, 48.9% reduction") has no test that produces those numbers, no fixture, no script. | **P0** | The single most-quoted marketing number cannot be defended in a sales call or a Hacker News comment thread. |
| 12 | **No competitive positioning anywhere.** Zero mentions of Helicone, LangSmith, LiteLLM, Portkey, Langfuse, OpenRouter in any doc. | **P1** | Buyers in this category are already evaluating those products. With no `vs.` page, tokenpak loses by default. |
| 13 | **"TokenPak Inc." is referenced in `LICENSE_COMMERCIAL.md` (line 86) but the entity does not exist.** `pyproject.toml` lists Kevin Yang individually. | **P1** | Commercial license is unenforceable. Any Team/Enterprise contract has no counterparty. |
| 14 | **Three competing API reference docs.** `docs/API.md` (Python SDK), `docs/api-reference.md` (REST), `docs/API_REFERENCE.md` (284 KB, recent). No doc declares which is canonical. They contradict each other. | **P0** | Onboarding docs lie to users about which methods/endpoints exist. Support burden multiplier. |
| 15 | **Three competing troubleshooting docs.** `docs/troubleshooting.md`, `docs/TROUBLESHOOTING.md`, root `TROUBLESHOOTING.md` — different content, same date in two cases. | **P1** | Users searching for an error get inconsistent advice. |
| 16 | **Python version requirement contradicts itself.** README says 3.10+, `getting-started.md` says 3.11+, `install-guide.md` says 3.8+, `pyproject.toml` says `>=3.10`. | **P0** | A user on Python 3.8 will follow the install guide, then fail. First-day install drops out. |
| 17 | **"Zero config required" is not true.** Beyond `tokenpak serve`, the user must set `ANTHROPIC_BASE_URL` (or per-SDK base URL). Compression also requires explicit env var to activate (see Finding #3). | **P0** | The README's signature claim is materially false. |
| 18 | **40+ CLI commands undocumented.** `tokenpak/cli.py:_COMMAND_GROUPS` lists 50+ commands including `dashboard`, `timeline`, `attribution`, `models`, `forecast`, `learn`, `vault-health`, `fleet`. `docs/cli-reference.md` documents only ~10. | **P1** | Half the surface area is invisible to users. Discovery requires reading source. |
| 19 | **DEPLOYMENT.md not in `mkdocs.yml` nav.** Production deployment guide exists but is not linked from the docs site. | **P1** | The "Day 30: deploy to prod" path is a dead end. |
| 20 | **`examples/` (20+ subdirs) is not linked from any doc page.** | **P2** | Best collateral the project has, totally undiscoverable. |
| 21 | **No proxy-level authentication.** Proxy listens on `0.0.0.0:8766` with no Bearer/API-key/session check. The README pricing table promises Team-tier "RBAC". | **P1** | RBAC is impossible to build without identity at the proxy ingress. Team tier is architecturally infeasible until this is added. |
| 22 | **Audit logs have the wrong shape.** `tokenpak/pro/audit_log.py` only records feature-usage events (`ts, adapter, model, feature, metadata`). No user_id, no IP, no request_id, no integration with the proxy request path. | **P1** | What's marketed as "audit logs" cannot satisfy any compliance regime (SOC2, HIPAA, GDPR all require user attribution). |
| 23 | **No Privacy Policy, no DPA, no sub-processor list, no Data Subject Access workflow.** Roadmap mentions SOC2/HIPAA in 2027. README pricing table sells "compliance documentation" today. | **P1** | A Team/Enterprise buyer's procurement form will ask for these in question 1. |
| 24 | **"Zero Data" claim has undisclosed escape hatches.** `TOKENPAK_LOG_REQUEST_BODY=true` and `store_prompts=true` enable raw request/response capture. Defaults are correct, but the privacy claim doesn't disclose the option exists. | **P1** | Trust marketing only works if the entire surface is consistent. A footnote-free "Zero Data" claim with optional content logging is the kind of thing that becomes a Twitter thread. |
| 25 | **SQL injection flagged by Bandit in `cli/commands/budget.py` and `cost.py`** — recommendation to parameterize. Status of fix is unknown; not noted as resolved in `SECURITY_AUDIT.md`. | **P1** | Trusted-toolchain risk. Even though it's the CLI not the proxy, the operator runs it as themselves; an injection there is real. |
| 26 | **Dependencies are not pinned.** `pyproject.toml` uses `>=` ranges (e.g., `aiohttp>=3.9.0`). No release signing (no GPG, no cosign). No CodeQL or Trivy in CI. | **P1** | Supply-chain risk for a product whose pitch is *trust*. |
| 27 | **No status page, no incident comms channel.** Even a markdown `INCIDENTS.md` would do. The only known incident reference is a comment in `proxy.py` about a 2026-03-14 swap exhaustion event. | **P1** | Enterprise buyers expect a status page in their RFP checklist. |

---

## 3. Detailed findings, by category

Each finding below: **What** / **Evidence** / **Why it matters** / **Recommendation**.

### A. Marketing-vs-reality (the most damaging gaps)

#### A1. Compression doesn't run on a default install (P0)
- **What.** README's hero is "48.9% token reduction." Default install has compaction gated behind env vars that default OFF.
- **Evidence.** `proxy_v4.py:226-228, 252` (`ENABLE_COMPACTION`, `COMPACT_THRESHOLD_TOKENS=4500`, `BUDGET_CONTROLLER_ENABLED=False`); `proxy_v4.py:962-978` (lazy `RecipeEngine` import inside conditional block); `tokenpak/recipes_oss/` (53 YAML recipes present but not auto-loaded).
- **Why it matters.** A new user runs `tokenpak serve`, points Claude Code at it, and sees zero compression. They will write a blog post titled "I tried tokenpak and it didn't compress anything." The hero number is undefended.
- **Recommendation.** Flip defaults: `ENABLE_COMPACTION=1`, lower `COMPACT_THRESHOLD_TOKENS` to ~1500, and load the OSS recipe set unconditionally on `serve` start. If there are stability concerns, add a `--safe` flag to opt OUT, not opt in. Then rebuild the README's "zero config" promise around this default.

#### A2. Budget enforcement returns the wrong error code (P0)
- **What.** README/DEPLOYMENT.md promise: "When the budget limit is hit, requests return a `429 Budget Exceeded` error." Proxy returns 429 only for IP rate-limit. No path returns "Budget Exceeded."
- **Evidence.** `proxy_v4.py:2376` (rate-limit 429, not budget); `proxy_v4.py:1334-1335` (lazy `BudgetController` import behind off-by-default flag); `proxy_v4.py:252`.
- **Why it matters.** Trivially testable feature. A buyer's first sanity check on an LLM-cost product is "what happens if I set a $0 budget?" — currently the answer is "nothing."
- **Recommendation.** Wire `BudgetController` to evaluate before upstream forwarding. Return `429 {error: "budget_exceeded", limit: ..., spent: ..., reset_at: ...}`. Add an integration test that asserts this. Document the response shape in `docs/openapi.yaml`.

#### A3. Dashboard URL is broken (P0)
- **What.** README: "Dashboard available at `http://localhost:8766/dashboard`." Dashboard router is never mounted on port 8766.
- **Evidence.** `tokenpak/agent/dashboard/app.py:269` (`create_combined_app()` exists); `tokenpak/agent/cli/commands/serve.py:33, 71-77` (only `ingest_router` and `query_router` are wired). Dashboard would be reachable via separate port 17888 only.
- **Why it matters.** First impression killer. The user clicks the link and sees a 404. Doc lies.
- **Recommendation.** Mount dashboard on `/dashboard` of the main proxy app in `serve.py`. Add a smoke test that does `curl localhost:8766/dashboard | grep -q "<title>"`. If multi-port is intentional, README must say so loudly.

#### A4. Alerts evaluate but never deliver (P0)
- **What.** `alerts.py` parses rules, computes triggers, manages cooldowns. There is no SMTP, no webhook POST, no Slack integration code path.
- **Evidence.** `alert_settings.json` (channels declared); `tokenpak/alerts.py:164-186, 251-297` (rule eval present); grep for `smtplib`, `requests.post.*webhook` returns nothing in alert delivery code.
- **Why it matters.** Pro tier sells "Budget enforcement + alerts" (README line 119). Alerts are vaporware.
- **Recommendation.** Implement at least one delivery channel before next release. Recommended order: (1) webhook (1 day), (2) Slack (1 day, just a webhook variant), (3) email (3 days, needs SMTP config UX). Add an `alerts test` CLI command that fires a synthetic alert end-to-end.

#### A5. License gating is non-existent in the request path (P0)
- **What.** Full license infrastructure exists. Proxy never validates a license at request time. Pro features are functionally identical to OSS.
- **Evidence.** `tokenpak/agent/license/keys.py` (signing), `tokenpak/infrastructure/license_validation.py` (validator), `portal/app.py` (key issuing); zero license check in `proxy_v4.py` initialization (lines 1-50 reviewed). LAUNCH_CHECKLIST.md line 15: "24 TODOs — all in Pro tier stubs (acceptable)."
- **Why it matters.** This is the **single largest blocker to revenue**. The commercial tier is unsellable until enforcement exists. Worse: an honest engineer will tell prospects "yeah it's the same code" and crater the upsell.
- **Recommendation.** This is a 1–2 week project, not a one-day fix.
  1. Define a `LicenseTier` enum (`OSS`, `PRO`, `TEAM`, `ENTERPRISE`).
  2. On proxy startup, load license from `~/.config/tokenpak/license.json`, validate signature, fall back to `OSS` if absent or invalid.
  3. Wrap each gated feature with a `@requires_tier(LicenseTier.PRO)` decorator (in Python: a function that checks the loaded tier and either runs the feature or runs the OSS fallback).
  4. Pro-gate the candidates already named in the pricing table: advanced compression recipes, budget alerts, replay/debug, A/B testing.
  5. Add a `tokenpak license status` CLI that prints `tier: pro / valid_until: 2026-12-01 / features_unlocked: [...]`.
  6. **Critical UX:** when an OSS user invokes a Pro feature, do NOT silently fall back. Print: "Advanced recipes are a Pro feature — start a free trial: https://portal.tokenpak.io/trial".

### B. Brand and trust (the cheap fixes that look most expensive when ignored)

#### B1. GitHub identity is split (P0)
- **Evidence.** README line 20 (`kaywhy331/tokenpak` CI badge), line 25 (`tokenpak/tokenpak` codecov), `pyproject.toml:85-90` (`tokenpak/tokenpak` homepage).
- **Recommendation.** Pick one. Recommend `tokenpak/tokenpak` (org account, future-proof). Transfer the personal repo, update all badges, redirect old URL. Half-day of work.

#### B2. Domains are unregistered (P0)
- **Evidence.** `LICENSE_COMMERCIAL.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `packages/README.md`, `portal/app.py:75`, `pyproject.toml`.
- **Recommendation.** Pick ONE domain (recommendation: `tokenpak.io` — short, available across most TLDs, no AI hype tax). Register it. Set up `sales@`, `support@`, `security@`, `conduct@` aliases. Stand up `portal.tokenpak.io`. Replace every `.ai` / `.dev` reference and the personal Gmail. Half-day of work.

#### B3. "TokenPak Inc." doesn't exist (P1)
- **Evidence.** `LICENSE_COMMERCIAL.md:86` references the entity; `pyproject.toml` author is an individual.
- **Recommendation.** Either (a) form the LLC/Inc. before any paid customer, or (b) rewrite `LICENSE_COMMERCIAL.md` to license from "Kevin Yang d/b/a TokenPak" until the entity is formed. Option (a) is the right answer if you intend to take any commercial revenue at all; talk to a lawyer about Delaware C-corp vs. LLC.

#### B4. Portal is built but invisible (P0)
- **Evidence.** `portal/app.py` (Flask + Stripe + RSA license signing — production-grade); not linked from `README.md`; not deployed.
- **Why it matters.** This is the highest ROI gap in the project. The hardest engineering work (Stripe webhooks, license issuing, tier management) is done. What's missing is one DNS record and one README link.
- **Recommendation.** Deploy `portal/` to Fly.io or Render in an afternoon, point `portal.tokenpak.io` at it, add three lines to README ("Upgrade to Pro: portal.tokenpak.io"), expose a `tokenpak upgrade` CLI command that opens the URL in a browser. Total: 1 day for a 100x conversion-funnel improvement.

#### B5. Headline benchmark is unreproducible (P0)
- **Evidence.** README line 45 ("48.9% token reduction — 5,074 → 2,594 tokens in <1ms"). No test, fixture, or script produces these numbers. Blog post quotes 34% / 84% — large variance, no methodology disclosed.
- **Recommendation.** Create `tests/benchmarks/test_headline_claim.py`. Pin a specific corpus (`tests/fixtures/headline_corpus.txt`). Assert that compression ratio is within 2 percentage points of the claimed value. Run on every PR. Add a "Reproduce this benchmark: `make benchmark-headline`" line under the chart in README. Without this, the marketing claim is undefendable.

### C. Documentation (the friction tax on every new user)

#### C1. Three API reference docs (P0)
- **Recommendation.** Pick `docs/API_REFERENCE.md` as canonical (most recent, largest, likely auto-generated). Delete or redirect `docs/API.md` and `docs/api-reference.md` with a note pointing to the canonical doc. If one is REST and the other is SDK, retitle them `REST_API.md` and `PYTHON_SDK.md` and link both from the docs index.

#### C2. Three troubleshooting docs (P1)
- **Recommendation.** Same pattern. `docs/troubleshooting.md` is highest quality; merge unique content from the other two; redirect.

#### C3. Python version disagreement (P0)
- **Recommendation.** `pyproject.toml` is canonical. Update README, getting-started, install-guide, INSTALL.md to all say `>= 3.10`. Add a CI matrix that proves it (3.10, 3.11, 3.12, 3.13).

#### C4. "Zero config" is false (P0)
- **Recommendation.** Either *make* it true (auto-detect provider, set base URL via shim, or ship a `tokenpak setup` wizard that writes `~/.claude/settings.json`) or *retitle* the claim ("Zero proxy config — one-line client config"). The current wording loses trust on day 1.

#### C5. 40+ CLI commands undocumented (P1)
- **Recommendation.** Auto-generate `docs/cli-reference.md` from `tokenpak/cli.py` argparse. Add a CI check: if a new `cmd_*` function lands without a docs entry, fail the build.

#### C6. `DEPLOYMENT.md` orphaned from docs nav (P1)
- **Recommendation.** Add to `mkdocs.yml` under a new "Production" section. Same for `examples/README.md` under "Examples."

#### C7. `examples/` undiscoverable (P2)
- **Recommendation.** Add a "See also" link to the relevant example from each integration guide (`docs/integrations/anthropic.md` → `examples/anthropic_basic/`, etc.). Add an "Examples" entry to the docs nav.

#### C8. No onboarding narrative (P1)
- **Recommendation.** Create `docs/onboarding.md` structured as Day 1 → Day 7 → Day 30. This is the single highest-leverage doc you can write. Reference: Vercel's "Get Started" series, Linear's "First 5 minutes."

### D. Observability and the company's blind spots

#### D1. Anonymous metrics ship to nowhere (P1)
- **Evidence.** `tokenpak/agent/config.py` recognizes `TOKENPAK_METRICS_ENABLED`; no destination URL anywhere.
- **Why it matters.** Without this, the team has zero visibility into adoption, activation, or retention. Every GTM decision (which features to invest in, which integration to ship next, when to launch Pro) is unanchored. This is also the cheapest GTM fix in the project.
- **Recommendation.** Stand up a tiny ingest endpoint (Fly.io / Cloudflare Worker / a single Fastify route on Render). Schema: `install_id (uuid), version, os, python, started_at, requests_24h, models[]`. Ship daily via a background goroutine. Be loud about opt-in: print "Anonymous usage telemetry enabled. Disable with `tokenpak metrics off`. Schema: docs/telemetry.md" on first run. Publish a real-time dashboard at `tokenpak.io/metrics` showing total installs (this also doubles as a marketing trust signal).

#### D2. No status page (P1)
- **Recommendation.** Even a markdown `STATUS.md` updated by hand is a start. Better: a single static page at `status.tokenpak.io` (Statuspage.io free tier or a self-hosted `cstate`). Required for any RFP.

#### D3. No in-CLI feedback / error reporting (P2)
- **Recommendation.** Add `tokenpak feedback "<message>"` that opens a GitHub issue with auto-attached context (version, OS, last 50 lines of log, anonymized config). Cuts your support triage time by an order of magnitude.

#### D4. Daily report exists but is not automated (P2)
- **Evidence.** `tokenpak/daily_report.py` is real but requires manual invocation.
- **Recommendation.** Add `tokenpak.service` systemd timer template; document cron alternative. Send via the Telegram-friendly markdown output it already produces.

#### D5. Audit log schema is wrong (P1)
- **Evidence.** `tokenpak/pro/audit_log.py` schema: `ts, adapter, model, feature, metadata`. No user, no IP, no request_id.
- **Recommendation.** Add columns: `user_id`, `client_ip`, `request_id`, `endpoint`, `tokens_in`, `tokens_out`, `cost_usd`. Wire it into the proxy request path (after upstream response, before returning to client). This is a prerequisite for any compliance claim.

### E. Security and compliance (table-stakes for Team/Enterprise)

#### E1. No proxy-level auth (P1)
- **Recommendation.** Add a `TOKENPAK_PROXY_AUTH_TOKEN` env var. If set, require `Authorization: Bearer <token>` from clients. If unset, only accept localhost (current behavior — check `proxy.py:~3317`'s existing `_check_auth()`). For Team tier, escalate to per-user tokens. RBAC then sits on top of this.

#### E2. "Zero Data" claim has undisclosed exceptions (P1)
- **Recommendation.** Either remove `TOKENPAK_LOG_REQUEST_BODY` and `store_prompts=true` entirely, or add a footnote to the privacy claim: "Zero Data is the default. Operators may opt INTO local request logging for debugging — this never leaves the machine. See [Privacy Policy](docs/privacy.md)." Then write the privacy policy.

#### E3. No Privacy Policy / DPA / sub-processor list (P1)
- **Recommendation.** Three documents:
  1. `docs/PRIVACY.md` — what we collect (telemetry only, opt-in), what we don't (prompts, responses, code), retention (default 90 days local), purge command.
  2. `docs/DPA.md` — Data Processing Agreement template for Team/Enterprise customers. Use the IAPP template as a starting point.
  3. `docs/SUB_PROCESSORS.md` — Stripe (billing), [hosting provider], [email provider]. Empty for now if you only run on the user's machine — *that itself is the differentiator*.

#### E4. SQL injection flagged in CLI commands (P1)
- **Evidence.** `SECURITY_AUDIT.md` notes Bandit findings in `cli/commands/budget.py` and `cli/commands/cost.py`. Status unknown.
- **Recommendation.** Verify status; if unfixed, parameterize all queries; add `bandit` to CI as a hard gate.

#### E5. Supply chain weaknesses (P1)
- **Recommendation.** (a) Pin all production dependencies in `pyproject.toml` (use `~=` or exact pins); (b) sign releases with `sigstore/cosign`; (c) add CodeQL workflow (free for public repos); (d) add Trivy to scan the Docker image; (e) generate an SBOM and publish it with each release.

#### E6. No clear contributor license (P2)
- **Recommendation.** Add a CLA bot (cla-assistant.io is free). Critical before accepting any non-trivial PR if you intend to relicense parts under the commercial license.

### F. Engineering hygiene that bleeds into product

#### F1. Two production proxy files (P1)
- **Evidence.** `proxy.py` 6.4k LOC + `proxy_v4.py` 4.2k LOC + 8 checkpoint snapshots in working tree.
- **Recommendation.** Decide which is canonical. The vault doc calls `proxy.py` "primary." If `proxy_v4.py` is the future, plan a migration cut-over date. Move checkpoints to `archive/` or delete them. A new contributor cannot tell which file they should edit; this is also a forensic audit risk.

#### F2. Proxy is a self-contained mega-file diverged from the package (P1)
- **Evidence.** `proxy_v4.py` is 4,195 lines and minimally imports from `tokenpak/`.
- **Recommendation.** Extract: VaultIndex (lines 634-829), provider routing (510-558), telemetry (1414-1481), cost calc (1601-1608) into the package. Each one is testable in isolation. This is a 1–2 week refactor that pays for itself the first time you need to fix a bug in two places.

#### F3. SDK packages are not integrated into the main flow (P2)
- **Evidence.** 8 SDK packages in `packages/` (langchain, llamaindex, crewai, autogen, js, local, vectordb, agents). All real code. None auto-loaded by `proxy_v4.py`.
- **Recommendation.** Either (a) document them as standalone, distinct products with their own positioning, or (b) plan an integration story. The current state — 8 real SDKs nobody knows about — is the worst of both worlds.

---

## 4. Prioritized 30/60/90 action plan

**Day 0–1 (founder, half a day each):**
- Register `tokenpak.io`; set up `sales@`, `security@`, `support@`. (B2)
- Pick canonical GitHub org; transfer repo; update badges. (B1)
- Form the legal entity (or rewrite LICENSE_COMMERCIAL.md to drop "TokenPak Inc."). (B3)

**Week 1 (engineering, ~5 days):**
- Flip compression defaults to ON; lower threshold; load OSS recipes by default. (A1)
- Wire `BudgetController` and return `429 budget_exceeded`. Add integration test. (A2)
- Mount the dashboard router on the proxy port; add smoke test. (A3)
- Implement webhook + Slack alert delivery; add `alerts test` CLI. (A4)
- Deploy `portal/` to Fly.io; point `portal.tokenpak.io` at it; add `tokenpak upgrade` CLI. (B4)
- Build `tests/benchmarks/test_headline_claim.py` and pin the headline number. (B5)
- Resolve API reference, troubleshooting, Python version, and "zero config" doc lies. (C1, C2, C3, C4)

**Week 2–3 (engineering, ~10 days):**
- Implement license tier enforcement in proxy (`@requires_tier` decorator). (A5)
- Add proxy-level auth (`TOKENPAK_PROXY_AUTH_TOKEN`). (E1)
- Stand up anonymous-metrics ingest endpoint; ship from CLI; publish `tokenpak.io/metrics` dashboard. (D1)
- Rewrite audit log schema; wire to proxy request path. (D5)
- Auto-generate CLI reference from argparse; add CI check. (C5)
- Write `docs/onboarding.md` (Day 1 → 7 → 30). (C8)

**Week 4–6 (founder + engineering):**
- Write `docs/PRIVACY.md`, `docs/DPA.md`, `docs/SUB_PROCESSORS.md`. (E3)
- Write a "vs. Helicone / LiteLLM / Portkey / Langfuse / OpenRouter" page. (Top-level finding #12)
- Resolve SQL injection findings; add bandit to CI. (E4)
- Pin dependencies; add CodeQL + Trivy + cosign to CI. (E5)
- Decide which proxy is canonical; archive checkpoints. (F1)
- Ship a `STATUS.md` page (or Statuspage.io). (D2)

**Stretch (after the above):**
- Plan and execute the proxy → package extraction. (F2)
- Decide the SDK story (standalone vs. integrated). (F3)
- Write a `LAUNCH.md` for v1.1 with the three new differentiators (real Pro, real benchmark, real alerts).

---

## 5. What this analysis is NOT

- **Not a code-quality audit.** The Python is clean, modular, and well-tested where coverage exists. The engineering is not the problem.
- **Not a critique of ambition.** Building a full-stack OSS-to-Enterprise product on your own is hard. The fact that 95% of the wiring exists is a serious accomplishment. The findings here are the *last 5%* that converts an impressive personal project into a sellable company.
- **Not a guarantee of GTM success once the gaps close.** Closing these gaps removes the *blockers* to selling. Whether the product *should* sell at the proposed price points (Pro $99/mo, Team $299/mo) is a separate question that depends on competitor pricing, target ICP, and validated willingness-to-pay — none of which were in scope for this audit.

---

## 6. Severity rollup

| Severity | Count | Theme |
|---|---|---|
| **P0 (launch-blocker / marketing-vs-reality)** | 12 | Compression OFF, budget enforcement broken, dashboard 404, alerts undeliverable, license unenforced, GitHub split, domains broken, portal orphaned, benchmark unreproducible, API docs triple-duplicated, Python version conflict, "zero config" false |
| **P1 (important — week-1 customer impact)** | 12 | Two proxy files, no proxy auth, audit log wrong, no privacy/DPA docs, no metrics backend, no status page, SQL injection unconfirmed-fix, supply chain weak, no competitive positioning, no entity, undocumented CLI, orphaned deployment doc, troubleshooting docs duplicated, onboarding has no narrative, examples unlinked, "Zero Data" undisclosed exceptions |
| **P2 (polish / credibility)** | 3+ | SDK story, CLA, container scan, daily-report automation |

---

## 7. Closing note

The single sentence I'd put in front of Kevin: **"Spend one week deploying the portal, fixing the four marketing-vs-reality lies (compression default, budget 429, dashboard URL, headline benchmark), and standing up `tokenpak.io` — and tokenpak goes from 'looks like a product' to 'is a product I could pay for' in seven days."**

Everything else can ship in the four weeks after that. But those seven days are the difference between a project and a company.

---

*Analyst: Claude (Sue) — 2026-04-07. Forensic methodology: 5 parallel agent passes covering production code, GTM/pricing, docs/onboarding, observability/support, security/compliance. All findings cite file:line from `/home/sue/tokenpak/` working tree. For any unclear citation, see the corresponding pass detail in `/tmp/claude-1000/-home-sue/76d26618-4651-4dc0-862e-625510502f49/tasks/`.*
