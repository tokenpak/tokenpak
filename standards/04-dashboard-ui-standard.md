---
title: TokenPak Dashboard UI Standard
type: standard
status: draft
depends_on: [00-product-constitution.md, 03-cli-ux-standard.md, 05-brand-style-guide.md]
---

# TokenPak Dashboard UI Standard

The dashboard is a local web UI that reads from `monitor.db`. It runs on the operator's machine; it never talks to a cloud backend. Its job is to make the TokenPak value story *visible* at a glance.

---

## 1. Audience and Job

**Primary audience:** a developer who just installed TokenPak and wants to see what it's doing and how much it's saving.

**Secondary audience:** an operator running TokenPak for a team, monitoring cost and health.

**Job to be done:** "In five seconds, tell me whether TokenPak is working and how much it saved today."

Not an analytics platform. Not a configuration tool. Not a credential manager. Those surfaces live elsewhere.

## 2. Information Hierarchy

The landing page is laid out in this order, top to bottom:

1. **Hero metric.** Today's savings, in tokens and dollars. Large, centered.
2. **Status strip.** Proxy up/down, creds OK, last request timestamp, queue depth.
3. **Compression chart.** Last 24h of original vs compressed tokens, stacked bar.
4. **Cache strip.** TokenPak cache hit rate · provider cache hit rate · `cache_origin` breakdown.
5. **Recent requests.** Table of the last 20 requests; columns: time, client, model, tokens in, tokens out, savings, origin.
6. **Quick actions.** Links to `tokenpak doctor`, docs, and the configured integrations.

Every other page is a drill-down from one of these six blocks. No orphan pages.

## 3. Visual Hierarchy

- **One hero number per page.** Never compete with yourself.
- **Secondary metrics** in a row beneath the hero. Same typographic rank as each other.
- **Supporting detail** below, scannable, denser type.
- Never more than three levels of visual weight on one screen.

## 4. Density

- **One chart per screen section.** Small-multiples require a dedicated comparison page.
- **Tables:** max 20 rows visible; the rest is pagination. No infinite scroll.
- **Whitespace before cleverness.** If the screen feels busy, remove something.

## 5. Signals

The dashboard's only job is to communicate state truthfully. Signals:

- **Healthy:** green dot + "up" + timestamp of last request.
- **Idle:** gray dot + "no requests in the last 5 min" — not alarming.
- **Degraded:** yellow dot + short cause ("proxy up, 1 cred expiring in 4h").
- **Error:** red dot + actionable cause ("proxy down: port conflict; run `tokenpak doctor`").

Same color semantics as CLI (see 03 §4.1).

## 6. Numbers

- **Round to the user's scale**, not more precision.
  - Tokens: no decimals below 10k; one decimal (k) for 10k–1M; one decimal (M) above.
    `8,412` · `14.9k` · `2.3M`
  - Dollars: cents if < $10, two decimals to $1000, no decimals above.
    `$0.73` · `$42.18` · `$1,204`
  - Percentages: one decimal. `32.8%`.
- **Never show raw floats.** `0.32847` is a bug.
- **Never show zero where data exists.** If a metric is missing, label it "not yet measured," not "0."
- **Every number has a unit.** No bare numbers.

## 7. Time

- All timestamps are **user-local by default**, UTC on hover.
- Relative time in tables: "2 min ago", "3 h ago", "yesterday".
- Absolute time in headers: "Today, Apr 18 · 20:29 PDT".
- Never mix — don't say "3:42 PM (2 min ago)" in the same cell.

## 8. Dashboard Tone

Labels and microcopy follow Constitution §8:

- **"Saved"**, not "Reduced" or "Shrunk."
- **"Cache"**, not "Memo" or "Memoized."
- **"Request"** is the unit. Not "call," not "transaction."
- **"Model"** for Anthropic/OpenAI/etc.; **"provider"** for the vendor; **"client"** for Claude Code, Cursor, Aider, etc.
- Error states start with the cause: "Port 8766 in use" beats "TokenPak could not start."

## 9. Attribution Truth

Per Constitution §5.3 and the `cache_origin` contract:

- Never claim a cache hit you can't attribute. `unknown` is a legitimate bucket and must be shown.
- The "savings" hero number is compression savings. Cache savings are a separate line, labeled as such.
- The provider cache and the TokenPak cache are two different numbers. The dashboard shows both, labeled.
- Over-claiming is worse than under-claiming. When in doubt, show less.

## 10. Empty States

Every view has a designed empty state. No blank panels.

| Surface | Empty state |
|---|---|
| No requests yet | "Send a request through TokenPak to see savings here. Run `tokenpak demo` to try it now." |
| No creds configured | "No provider credentials discovered. Run `tokenpak creds doctor` to see what TokenPak found." |
| Proxy down | "Proxy is not running. Start it with `tokenpak serve`." |
| Dashboard disconnected | "Can't reach `127.0.0.1:8766`. Check the proxy, or your firewall." |

## 11. Performance

- **Dashboard loads in under 500ms** on the operator's local machine. The monitor DB is local; there's no excuse for slowness.
- **Live updates** via polling, not WebSocket. Poll interval 5s. Polling stops when the tab is backgrounded.
- **No external resources.** No CDN fonts, no analytics scripts, no remote images. The dashboard must work offline.

## 12. Styling

Visual details — colors, type scale, spacing — live in `05-brand-style-guide.md`. This document specifies *what* to show; the brand guide specifies *how* it looks.

## 13. What Not to Build

These are explicit non-goals for the dashboard:

- **Log viewer.** Use `journalctl` or `tail -f`.
- **Config editor.** Use `tokenpak config` or the YAML files.
- **Credential UI.** Use `tokenpak creds`.
- **Chat UI.** Not TokenPak's surface.
- **Team / multi-seat analytics.** Out of scope for this dashboard — the monitor DB is per-machine.
