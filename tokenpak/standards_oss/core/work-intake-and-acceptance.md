---
id: BS-CORE-WORK-INTAKE-AND-ACCEPTANCE
layer: core
risk_class: high
default_coverage_profiles: [starter, delegated-work, product-delivery, multi-agent]
control_points: [work.accept, work.scope-change]
---

# Work intake, scoping, and acceptance

## Purpose

Define what a unit of work is, when it is allowed to start, and what has to be true before anyone
calls it done — for any kind of deliverable, not only code.

## Applies to

All work, all modes. **Read now.**

This standard is deliberately deliverable-agnostic. A decision, a memo, an analysis, a design, a
client email, a configuration change, a payment, and a code change are all units of work, and all of
them can be accepted badly.

---

## Requirements

### Intake

**R1.** An idea is not a unit of work until it has: a stated outcome, an owner, and acceptance
criteria. Until then it is a proposal, and MUST be held as one.

**R2.** Acceptance criteria MUST be written **before** the work starts, by someone other than the
sole executor where the work is consequential. Criteria written afterwards describe what was
produced.

**R3.** Every unit of work MUST declare its **deliverable class** (see the table below). The class
determines what evidence acceptance requires.

**R4.** Every unit of work MUST declare its **envelope**: what it may touch, what it must not touch,
what it may spend, and how long it may run. An unbounded task has no failure condition — it can only
be abandoned, never completed.

**R5.** Work MUST NOT begin outside a declared envelope. Discovering mid-work that the envelope is
too small is a `work.scope-change`, not a judgement call for the executor.

### Deliverable classes and their evidence

**R6.** Evidence requirements attach to the deliverable class, not to the mode:

| Deliverable class | Accepted when |
|---|---|
| **Decision** | Alternatives recorded, reasoning stated, authority confirmed, record written |
| **Document or analysis** | Claims traceable to sources; assumptions stated; someone other than the author has read it against its criteria |
| **Recommendation** | The evidence behind it is inspectable and its limits are stated |
| **Code or configuration change** | Behaviour verified by evidence proportional to risk (see the software-delivery module); prior behaviour unbroken |
| **External communication** | Claims substantiated; commitments within capacity; authorized by whoever owns the relationship |
| **Financial action** | Amount and destination verified; obligation basis recorded; authorization specific to this instance |
| **Operational change** | Prior state captured; effect observed; reversal path known |

**R7.** The evidence bar MUST NOT be lowered because the work is late, small, or urgent. Those are
reasons to reduce **scope**, never to reduce **evidence**.

**R8.** A class MUST NOT be self-assigned downward to reduce the evidence required. Where the
executor picks the class, the reviewer confirms it.

### Acceptance

**R9.** **The executor does not accept its own work.** Whoever produced the deliverable does not
decide it is done. This holds in every authority profile, for humans and agents alike.

**R10.** Acceptance MUST be against the criteria written at intake. Discovering at acceptance that
the criteria were wrong is a legitimate finding — it produces a scope change and new criteria, not a
quiet re-scoring against what was actually delivered.

**R11.** Acceptance MUST be recorded: who accepted, against which criteria, what evidence they saw.

**R12.** Partial acceptance MUST be explicit — which criteria are met, which are not, and what
happens to the remainder. "Accepted with follow-ups" without naming the follow-ups is not
acceptance.

**R13.** A defect found after acceptance MUST produce a check for the same defect class elsewhere in
the work, and a change to the criteria that let it through.

### Scope changes

**R14.** A material change to the outcome, the envelope, or the acceptance criteria is a
`work.scope-change` and MUST be authorized before continuing.

**R15.** A scope change **invalidates prior authorizations** that were granted against the old scope.
Re-authorize what still applies.

**R16.** Scope reduction is a legitimate and often correct response to constraint. It MUST be
recorded as a decision, not applied silently by delivering less than was agreed.

### Stopping

**R17.** An executor encountering a contradiction, an unauthorized protected action, a missing
declaration, or an envelope breach MUST **stop and escalate**. Stopping requires no authorization in
any profile and is never a violation.

**R18.** Work stopped mid-flight MUST leave a recoverable state and a record of where it stopped and
why. A stop that destroys context costs more than the work it prevented.

---

## Evidence and acceptance

For this standard itself: a unit of work is well-formed when someone other than its executor can
state its outcome, its envelope, its class, and its acceptance criteria — without asking the
executor.

## Control points

| Control | Relevance here |
|---|---|
| `work.accept` | Who accepts, and with what independence, per authority profile |
| `work.scope-change` | Who may change scope, and what that invalidates |

## Exceptions and stop conditions

Expedited work (incidents) MAY compress intake to: outcome, owner, envelope, and stop condition —
recorded at the time. Acceptance criteria may follow. What is deferred MUST be recorded and repaid.

There is no exception to R9. Self-acceptance under time pressure is how unreviewed work reaches
production, and time pressure is precisely when it is most likely to be wrong.

## Anti-patterns

- "Just look into it" as a task — no outcome, no envelope, no stop condition.
- Acceptance criteria written by the executor after the work is finished.
- Reclassifying a change as low-risk to skip review.
- An agent marking its own task complete.
- Expanding scope mid-task because it seemed obviously in the spirit of the request.
- "Accepted with follow-ups" where the follow-ups are never written down.
- Reducing evidence rather than scope when time runs out.

## Templates

`templates/task.md` · `templates/acceptance-record.md` · `templates/decision-record.md`

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
