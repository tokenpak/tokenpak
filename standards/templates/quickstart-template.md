---
title: Quickstart
rung: 1
audience: Developers who have heard of TokenPak and want to try it in the next five minutes.
updated: <YYYY-MM-DD>
status: draft
---

<!--
A quickstart takes the reader from zero to a working, provable result. End at a demo they can see with their own eyes, not a "now read the docs" cliff.

Target length: 1 scroll on a laptop. If it doesn't fit, you're including material that belongs in a Guide.

Delete this comment before committing.
-->

# Quickstart

This guide takes you from `pip install` to a working TokenPak demo in **five minutes**.

## What you'll have at the end

- TokenPak running locally on port 8766
- <Client> wired to TokenPak with zero code changes
- A compression demo showing real token savings

## Prerequisites

- Python 3.10+
- `pip`
- <Any client-specific prereq — e.g., "Claude Code 1.x installed">

## 1. Install

```bash
pip install tokenpak
```

**Verify:**

```bash
tokenpak --version
```

```text
tokenpak <version>
```

## 2. Start the proxy

```bash
tokenpak serve
```

```text
✓ TokenPak proxy listening on http://127.0.0.1:8766
✓ Monitor DB: ~/.tokenpak/monitor.db
```

Leave it running. Open a second terminal for the next steps.

## 3. Wire a client

<!-- Replace `<client>` with the primary client for this quickstart. One quickstart per client if needed; link between them. -->

```bash
tokenpak integrate <client> --apply
```

```text
✓ Applied: Updated <client config path> (<N> changes).
```

## 4. See savings on a real prompt

```bash
tokenpak demo
```

```text
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

## 5. Use your client

Open `<client>` and use it as you normally would. TokenPak is transparent — you'll see the usual responses, at a lower token cost.

**Verify from the monitor:**

```bash
tokenpak savings
```

```text
┌──────────────────────────────────────────────────────┐
│  TokenPak — Savings (today)                          │
├──────────────────────────────────────────────────────┤
│  Requests observed                                 1 │
│  Saved tokens                                    245 │
│  Saved (est.)                               $0.00073 │
│  Cache origin       proxy: 1    client: 0    unk: 0  │
└──────────────────────────────────────────────────────┘
```

## What's next

- [Guide: wire a second client](../docs/guides/<add-client-guide>.md)
- [Dashboard tour](../docs/guides/<dashboard-tour>.md) — visualize savings over time
- [API reference](../docs/api-tpk-v1.md) — wire TokenPak into your own code

## Troubleshooting

Something not working? Start here:

| Symptom | See |
|---|---|
| `tokenpak serve` reports port in use | [Port conflict](../docs/troubleshooting/port-in-use.md) |
| `tokenpak integrate` made no changes | [Integration dry-run](../docs/troubleshooting/integration-no-changes.md) |
| Demo shows 0 savings | [Zero savings in demo](../docs/troubleshooting/zero-savings.md) |

Full list: [Troubleshooting](../docs/troubleshooting/).

<!--
Quickstart audit:
- Does every command work exactly as written today?
- Does the reader see a real number by the end?
- Have you kept it to one scroll?
- Have you avoided any rung-3 material (API reference)?
-->
