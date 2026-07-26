---
id: BS-DELEGATION-ROLES-AND-AUTHORITY
layer: delegation
risk_class: high
default_coverage_profiles: [delegated-work, product-delivery, multi-agent]
control_points: [access.grant-escalation, escalation.stop, work.scope-change]
---

# Roles and authority

## Purpose

Say who may do what, on whose authority, within what bounds — so that work can proceed without you
while still being work you would have authorized.

## Applies to

Any work performed by someone or something other than you alone. **Read now if agents or other
people do the work.**

---

## Requirements

### Roles are functions

**R1.** Assign the five functions in `GOVERNANCE.md` section 3 — operator, delegate, reviewer, executor,
auditor — to actual actors. One person may hold several. An agent may hold executor, and may hold
reviewer for work it did not produce.

**R2.** No actor holds all of executor, reviewer, and authorizer for the same unit of work. This is
the one combination that removes every check simultaneously.

**R3.** Maintain a **single register of actors**: who or what may act, in which role, with what
permissions, and who is accountable for each. One register, not one per system.

**R4.** An actor not in the register MUST NOT be treated as authorized. Unknown actors **fail
closed**. This matters most when it is inconvenient — an unregistered process acting during an
incident is exactly the case the rule exists for.

**R5.** The register MUST be checked against reality on a stated cadence. Registers drift silently;
the drift is the finding.

### Delegation

**R6.** A delegation MUST state: who grants it, to whom, for what class of action, within what
envelope, and until when. Missing any of these means no delegation exists.

**R7.** **Delegated authority never exceeds the granting authority**, and never exceeds the
delegate's actual permissions. Granting authority an actor cannot exercise is a configuration defect.

**R8.** Delegation is not transitive by default. A delegate MUST NOT sub-delegate unless the grant
says so explicitly, and a sub-grant is bounded by the original.

**R9.** Delegations expire. A grant with no expiry is a defect, not a convenience. Renewal is a fresh
decision, and renewing is when you notice a grant is no longer needed.

**R10.** Delegation MUST NOT cross a protected category (`GOVERNANCE.md` R12). You cannot delegate
what is non-delegable — that is what the word means.

### Judgement within a delegation

**R11.** State what a delegate does when the situation is not covered. The options are: proceed
within a named envelope, hold and ask, or stop. "Use your judgement" without bounds is not a
delegation — it is an abdication.

**R12.** The bound SHOULD scale with reversibility. Wide latitude on reversible actions; narrow to
none on irreversible ones. This is the practical form of the whole risk model.

### Escalation

**R13.** Every escalation path MUST name: who is reached, how, within what window, and **what happens
if they do not respond**.

**R14.** **The escalation target is not the default blocker.** An unanswered escalation resolves to
one of two pre-declared outcomes — proceed within a named envelope, or stop cleanly. Never "wait
indefinitely"; never "assume approval".

**R15.** Incidents affecting the machinery that carries escalations MUST NOT be escalated through
that machinery. Name an out-of-band path in advance, and test that it works.

**R16.** Escalation MUST NOT be penalised. An actor that escalates correctly and turns out to have
been over-cautious behaved correctly.

### Stopping

**R17.** Every actor can stop work at any time, without authorization, in any authority profile.

**R18.** There MUST be an **emergency stop**: a way to halt all delegated work immediately, known to
everyone, exercisable by a human without dependence on the systems being stopped.

**R19.** The stop path MUST be tested. An untested emergency stop is a belief, not a control.

**R20.** Stops SHOULD be layered — stop one task, stop one actor, stop everything — so the response
can be proportionate. If the only stop is "stop everything", people hesitate to use it, which is the
same as not having one.

---

## Evidence and acceptance

You can answer, from the register and the grants alone: who may take each protected action, on whose
authority, until when — and demonstrate the emergency stop working.

## Control points

| Control | Relevance here |
|---|---|
| `access.grant-escalation` | Protected. Widening permissions is authorized, bounded, and expires |
| `escalation.stop` | Never requires authorization, in any profile |
| `work.scope-change` | Where a delegate hits the edge of its envelope |

## Exceptions and stop conditions

**Stop** when an actor is not in the register, a grant has expired, a grant is ambiguous about a
protected action, or the escalation path is unavailable and no fallback was declared.

## Anti-patterns

- "Full access, it's easier" — then a year later nobody can say who may do what.
- Grants with no expiry, accumulating until the register describes nobody's current job.
- An agent holding a credential it needs only to *prepare* an action a human must perform.
- Escalation to a person who is asleep, with no declared fallback — work stalls or improvises.
- An emergency stop that requires the system it is stopping.
- Penalising an actor for escalating, then wondering why it stopped escalating.

## Templates

`templates/operator-onboarding.md` · `templates/decision-record.md`

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
