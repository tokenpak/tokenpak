# TokenPak Dispatch (v0.1-alpha preview)

> **Preview status.** Dispatch is an early **v0.1-alpha preview**. It is **not yet part of a
> released `pip install tokenpak` package** — in published PyPI wheels the Dispatch engine is
> intentionally excluded, so the `dispatch` command is cleanly absent. To try the preview today you
> need a **source / `main`-branch install** (see [Trying the preview](#trying-the-preview)). The
> command surface is real and tested, but one part of the flow — actually executing the work and
> producing a delivery receipt — is intentionally **post-alpha** and is not wired yet.

## What Dispatch is

Dispatch is a CLI-first **workflow-control surface** for turning a plain-language request into a
scoped, inspectable, resumable work package.

You hand Dispatch a request like *"add input validation to the signup form and open a PR"*. Dispatch
reads the intent, picks a **route**, records its assumptions and any missing information, and — when
the request needs a human call — raises a card in a **Decision Inbox** for you to approve or reject.
Everything it does is written to a local **Run Ledger**, so a job can be paused, resumed, inspected,
or cancelled at any time.

The goal is to make agentic work **legible and controllable**: you always have a record of what was
proposed, why, and at what level of autonomy — rather than a black box that runs to completion.

## Core concepts

- **Route** — the workflow a request is matched to (for example a code task vs. a review task). A
  route determines which stations a job passes through. Auto-routing picks one from the request; you
  can force a specific route with `--route`.
- **Station** — a single stage within a route (for example "implement" or "review"). Stations are how
  a job is broken into ordered, individually-recorded steps.
- **Decision Inbox** — the human-in-the-loop queue. When a job hits something that needs a person —
  an approval-gated route, a risky action, or a blocking gap — Dispatch parks a **decision card** here
  with a clear recommendation. You clear it with `approve` or `reject`.
- **Autonomy mode** — how much Dispatch is allowed to do on its own for a given job:
  - `advisory` — analyze and advise only.
  - `draft` — produce a draft, take no action (this is what `--dry-run` selects).
  - `dispatch_with_approval` — the default for an interactive run; act only after you approve.
  - `auto_dispatch_limited` — a bounded automation mode for CI/non-interactive callers (selected by
    `--ci`).
- **Run Ledger** — the local record of every job: its status, routes, decisions, and stations. It is
  what makes jobs resumable and inspectable, and it is created automatically on your first run.
- **Delivery & Receipt** — the *intended* end of the flow: a job's **Delivery Package** is the work
  product, and the **Receipt** is the signed confirmation that it was delivered. See the honest
  caveat below — this happy path is post-alpha.

## What works today

From the CLI, the **control plane is fully usable** in the preview:

| You want to… | Command |
|---|---|
| Intake and route a request into a job | `tokenpak dispatch run "<request>"` |
| See what would happen without writing a job | `tokenpak dispatch run "<request>" --dry-run --json` |
| Check a job's current status | `tokenpak dispatch status <job-id>` |
| See a job's full record (routes, decisions, stations) | `tokenpak dispatch inspect <job-id>` |
| List open Decision Inbox cards | `tokenpak dispatch decisions` |
| Approve / reject a decision | `tokenpak dispatch approve <id>` · `tokenpak dispatch reject <id>` |
| Pause / resume a job | `tokenpak dispatch pause <job-id>` · `tokenpak dispatch resume <job-id>` |
| Cancel a job (late results handled) | `tokenpak dispatch cancel <job-id>` |
| Discard a late result | `tokenpak dispatch discard-late <job-id>` |

Every command supports `--json` for scripting, returns clear exit codes, and gives a readable error
(for example `✗ no such job`, exit `1`) on bad input.

### Try it

A dry run never touches the ledger and shows you the routed **job card**:

```console
$ tokenpak dispatch run "review the auth changes in this branch" --dry-run
Dispatch run
────────────
  Job        : job_01J...
  Intent     : review_task
  Autonomy   : draft
  Route      : review_task (route_...)
  Selection  : selected  [layer=..., confidence=...]
```

A normal run records the job and, if the route is approval-gated, points you straight at the
Decision Inbox:

```console
$ tokenpak dispatch run "implement and open a PR for the new endpoint"
...
  Decision Inbox:
    • dec_01J...  →  tokenpak dispatch approve dec_01J...
```

## What is intentionally post-alpha

**Delivery and receipts are not wired in this preview.** The step that actually executes a station's
work is not connected to the CLI yet, so a job never reaches a delivered state through the commands
above. This is by design for v0.1-alpha — it is the headline capability the next milestone will add.

Because of that:

- `tokenpak dispatch delivery <job-id>` and `tokenpak dispatch receipt <job-id>` will **report that no
  receipt is produced in this preview build**, rather than appearing broken.
- In `--json` they return a stable contract (`error: "no_receipt"`, `delivered: false`) so scripts can
  detect the preview state cleanly.

If you see "no receipt" in the preview, that is expected and correct — not a bug.

## Trying the preview

Because the engine is excluded from released wheels, `pip install tokenpak` will **not** give you a
`dispatch` command. To explore the preview, install TokenPak from source on the project `main`
branch, then run:

```console
$ tokenpak dispatch --help
```

If `dispatch` is not listed, you are on a released package without the preview engine — switch to a
source / `main`-branch install. When Dispatch graduates beyond the preview, it will become available
through the standard install.

## Summary

- Dispatch is a CLI-first, preview workflow-control surface: route a request, track it in a Run
  Ledger, and approve/reject via a Decision Inbox.
- The control plane (run, status, inspect, decisions, lifecycle, error paths) **works today** from a
  source/`main` install.
- Live station execution and delivery **receipts are post-alpha** and not wired yet.
- It is **not** available via `pip install tokenpak` in this preview.
