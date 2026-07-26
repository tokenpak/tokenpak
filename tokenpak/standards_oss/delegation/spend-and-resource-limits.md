---
id: BS-DELEGATION-SPEND-AND-RESOURCE-LIMITS
layer: delegation
risk_class: critical
default_coverage_profiles: [starter, delegated-work, product-delivery, multi-agent]
control_points: [spend.paid-action, spend.limit-change, finance.payment-authorize]
---

# Spend and resource limits

## Purpose

Bound what delegated work can consume, and make the bound hold when measurement fails — which is
exactly when it is needed.

## Applies to

Any work that consumes a metered resource: money, API calls, compute, quota, time. **Configure now.**
This is in the starter profile because unbounded consumption is the fastest way an autonomous system
causes damage that cannot be undone by stopping it.

---

## Requirements

### Two bands

**R1.** Declare two limits per metered resource:

| Band | Behaviour at the limit |
|---|---|
| **Soft** | Warn, notify, continue. A signal, not a barrier. |
| **Hard** | Stop cleanly. Not bypassable by the actor that hit it. |

**R2.** A hard limit that the consuming actor can raise is not a hard limit. `spend.limit-change`
requires the operator in **every** authority profile, including `bounded-autonomous`.

**R3.** Every limit has a **unit and a window**. "Budget: 100" is not a limit — 100 what, per what?

**R4.** Hitting a hard limit is a **successful stop**. Report it as such. An executor that treats its
limit as an obstacle to work around does not have a limit.

### Failing closed

**R5.** **When consumption cannot be measured, spending MUST stop.** Not "continue and reconcile
later" — the failure mode of measurement is silence, and silence reads as zero (see
`BS-CORE-TRUTH-AND-EVIDENCE` R1).

**R6.** A measurement failure MUST be reported as a measurement failure, never as consumption within
budget.

**R7.** One exception, narrowly: a legitimately fresh start with no history yet MAY proceed, provided
the absence of history is confirmed rather than assumed, and the first measurement establishes the
baseline. This exception MUST NOT be used to explain away a missing measurement in an established
workload.

### Operating contracts by context

**R8.** How a limit behaves depends on whether a human is present:

| Context | Contract |
|---|---|
| **Interactive** — a human is watching | Confirm before exceeding soft; never silently pass hard |
| **Unattended** — no human present | Budget declared **before** starting; exit cleanly at the limit; never retry into the limit |
| **Hybrid** — a human is reachable | Time-limited grants with explicit expiry; on no response, the pre-declared fallback applies |

**R9.** An unattended actor MUST NOT retry against a limit. Retry loops against a resource ceiling
convert a stop into sustained consumption, and they are the single most expensive failure pattern in
delegated work.

**R10.** An unattended actor MUST NOT prompt. There is nobody there; a prompt is a hang. Fail closed
and exit.

### Paid effects happen once

**R11.** Before an action that costs money, **record the intent** — what, how much, to whom, under
which authorization. Record before, not after.

**R12.** After the action, record the outcome against the same intent record.

**R13.** Where the outcome is **ambiguous** — timeout, unclear response, interrupted connection — the
action MUST NOT be retried automatically. Ambiguous means "may have happened", and retrying an
already-charged action charges twice.

**R14.** Ambiguous paid effects MUST be resolved by inspecting the authoritative source, not by
inference from local state.

**R15.** Every paid action MUST be attributable to a unit of work and an authorizing basis. Spend
that cannot be attributed cannot be governed.

### Visibility

**R16.** Consumption MUST be visible to the operator without asking the consuming actor. Self-
reported spend has the same defect as self-accepted work.

**R17.** Report consumption against the limit, not in isolation. "$40 spent" is data; "$40 of a $50
daily limit, at 14:00" is a decision input.

**R18.** Where installing or running something incurs cost, that MUST be disclosed before it is
incurred, not discovered afterwards.

---

## Evidence and acceptance

You can state, without asking the executor: the limits per resource, current consumption against
them, what happens when measurement fails, and where the intent ledger for paid actions lives.

## Control points

| Control | Relevance here |
|---|---|
| `spend.paid-action` | Standing envelope in most profiles; the envelope *is* the budget |
| `spend.limit-change` | Operator in every profile — a system that raises its own ceiling has none |
| `finance.payment-authorize` | Protected. Per-payment authorization; no standing authority |

## Exceptions and stop conditions

**Stop** when: a hard limit is reached; measurement is unavailable and this is not a confirmed fresh
start; a paid action's outcome is ambiguous; or spend would be unattributable.

Expedited paths do **not** widen budgets. An incident that requires more resource requires a
`spend.limit-change` decision, recorded, with an expiry — during the incident, briefly.

## Anti-patterns

- One limit, treated as advisory, exceeded routinely.
- Continuing when the meter breaks, on the grounds that usage was fine yesterday.
- An unattended job prompting for confirmation, then hanging until it is killed.
- Retrying into a quota ceiling until the quota window resets.
- Retrying a timed-out payment "to be safe".
- Spend visible only through the executor's own report.
- A limit in tokens when the bill is in currency, with no mapping between them.

## Templates

`templates/decision-record.md` (for limit changes) · `templates/operator-onboarding.md` (budgets
section)

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
