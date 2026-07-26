---
id: BS-COMMITMENTS-STAKEHOLDER-AND-SUPPORT
layer: commitments
risk_class: high
default_coverage_profiles: [delegated-work, product-delivery, multi-agent]
control_points: [commit.external-promise, work.scope-change, data.disclose-external]
---

# Stakeholder commitments and support

## Purpose

Govern what you promise to people outside your control — clients, customers, partners, users,
collaborators — and what happens when a promise meets reality.

## Applies to

Any work with an outside party who may rely on what you say. **Read now if you work for or with
anyone else.**

This is the standard most often missing from operating frameworks written for engineering teams. The
commitments that damage a business are rarely technical.

---

## Requirements

### Making a commitment

**R1.** A commitment is anything an outside party may reasonably rely on: a date, a scope, a
capability, a price, a response time, a level of access. Whether it is contractual is not the test —
**reliance is the test**.

**R2.** A commitment MUST be authorized by whoever owns the relationship and bears the consequence of
breaking it. `commit.external-promise` stays with the operator in every authority profile, including
`bounded-autonomous`.

**R3.** **Agents MUST NOT make commitments on your behalf** unless the commitment is within an
explicit, bounded, expiring delegation naming exactly what may be committed. Drafting a message is
not the same as sending it; the boundary between them should be structural.

**R4.** Before committing, verify **present capacity to honour it**. Not intended capacity, not
capacity if the plan works.

**R5.** Commitments MUST be recorded in a single register: what was promised, to whom, by whom, when,
by when, and on what conditions. Commitments scattered across conversations get broken by people who
never knew about them.

**R6.** Where a commitment depends on the other party doing something, that condition MUST be stated
to them at the time, not raised later as an excuse.

### Scope with outside parties

**R7.** Agreed scope changes only through a recorded, mutually acknowledged decision. Silent scope
expansion to keep someone happy is the most reliable way to make them unhappy later.

**R8.** Additional work outside agreed scope MUST be surfaced as a decision — accept, decline, or
re-scope — not absorbed quietly. Absorbed work resets expectations without anyone deciding to.

**R9.** Where scope is reduced, say so explicitly and early. Delivering less than agreed without
saying so is a broken commitment even if the reduction was reasonable.

### When a commitment cannot be met

**R10.** Tell them **as soon as you know**, not when the deadline arrives. The cost of a missed
commitment is mostly the cost of the surprise.

**R11.** The message states: what will not happen, what will, by when, and what you are doing about
it. Not an apology in place of information.

**R12.** Repeatedly missed commitments of the same kind are a signal about how you estimate or
commit, and MUST produce a change in practice rather than a better apology.

### Support and responsiveness

**R13.** Stated support obligations MUST NOT exceed present capacity (`R4`). A published response
time is a commitment to every person who reads it.

**R14.** Where you cannot commit to a response time, say what you can: best-effort, business hours,
no guarantee. An honest limit beats an aspirational promise.

**R15.** Support requests MUST have a stated intake path and an acknowledgement behaviour, including
what happens when nobody is available. Silence is a response, and it is the worst one.

**R16.** Escalating a support obligation — faster response, wider scope, dedicated attention — is a
**resourced decision**, not a favour granted in the moment. It changes capacity for everyone else.

**R17.** Where an agent handles stakeholder communication, its envelope MUST state what it may say,
what it must not commit to, and when it must hand off to a human. Default: it may inform, and it may
not promise.

### Confidentiality in the relationship

**R18.** Information received from a stakeholder is theirs. Disclosure outside the agreed boundary is
`data.disclose-external` — protected. Reusing a client's material in another engagement without
permission is a disclosure, whether or not it is labelled confidential.

---

## Evidence and acceptance

You can produce, for any outside party: what has been committed to them, by whom, when, its
conditions, its current status — from the register, without reconstructing it from message threads.

## Control points

| Control | Relevance here |
|---|---|
| `commit.external-promise` | Operator authority in every profile; capacity verified first |
| `work.scope-change` | Scope movement with an outside party is always a recorded decision |
| `data.disclose-external` | Protected. Their information stays inside the agreed boundary |

## Exceptions and stop conditions

**Stop** when: a commitment is being made without capacity to honour it; an agent is about to send a
commitment outside its envelope; scope is drifting without acknowledgement; or you are about to reuse
one party's information for another.

## Anti-patterns

- A date given in a call and never recorded anywhere.
- An agent replying to a client with a delivery estimate it inferred.
- Absorbing extra scope repeatedly to be accommodating, then missing the original scope.
- Telling them at the deadline, not when you knew.
- Publishing a response time nobody is rostered to meet.
- Granting one client an escalated obligation without accounting for the capacity it takes.
- Reusing a client deliverable as a template for another client's work.

## Templates

`templates/decision-record.md` · `templates/operator-onboarding.md`

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
