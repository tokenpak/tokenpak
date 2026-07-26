---
id: BS-CONTINUITY-INCIDENTS-AND-ESCALATION
layer: continuity
risk_class: critical
default_coverage_profiles: [starter, delegated-work, product-delivery, multi-agent]
control_points: [escalation.stop, recovery.rollback, governance.waiver]
---

# Incidents, escalation, and exceptions

## Purpose

Handle things going wrong without abandoning the discipline that keeps them from going worse.

## Applies to

Everyone. **Read now** — this is not a chapter to reach later. Incident procedure read for the first
time during an incident is not a procedure.

---

## Requirements

### Declaring

**R1.** Anyone may declare an incident. Declaring one that turns out to be minor is correct
behaviour, and MUST NOT be discouraged.

**R2.** An incident has one **named coordinator** — a person. Coordination is a role, not a
committee, and it is separate from doing the technical work.

**R3.** State the impact in terms of who is affected and how, not in terms of which component failed.
"Component X is down" does not tell anyone whether to act.

**R4.** Incidents in the machinery that carries escalations MUST route **out of band**. Name that
path in advance and test it. An alerting failure reported through the alerting system is not
reported.

### Acting

**R5.** **Stabilise before diagnosing.** Stop the harm first; understand it second.

**R6.** Capture the current state **before** changing it. The evidence you need for the postmortem is
destroyed by the fix, and it is destroyed first.

**R7.** Every action taken during the incident is recorded **as it happens** — what, by whom, when,
why. Not reconstructed afterwards from memory that will already be wrong.

**R8.** Actions that could worsen the situation MUST be stated before being taken, so someone can
object while objecting is still useful.

**R9.** **Incidents do not suspend protected actions.** They are precisely when protected boundaries
earn their cost: pressure, urgency, and a plausible reason to skip a step. If a protected action is
needed, get the authorization — this is what break-glass is for, and break-glass is logged and
reviewed.

**R10.** Rolling back is `recovery.rollback` — protected. Rapid does not mean unauthorized.

### Expedited, not exempt

**R11.** Expedited paths MAY reorder steps, compress review, and defer non-blocking evidence. They
MUST NOT skip authorization for protected actions, and MUST NOT lower correctness requirements.

**R12.** Everything deferred MUST be **recorded at the time** and repaid within a stated window.
"We'll write it up later" without a record and a date means it will not be written up.

**R13.** Every expedited path names who may invoke it. Anyone-may-invoke means it will become the
normal path, and then there is no normal path.

**R14.** Expedited invocations MUST be counted. Frequency is a finding about the normal path, not a
statistic to file.

### After

**R15.** Every incident above a stated threshold gets a **written review**, within a stated window,
covering: what happened, the timeline, why, what made it worse, what made it better, and what changes.

**R16.** Reviews examine **conditions**, not individuals. "Who made the mistake" reliably produces
less information next time; "what made the mistake easy to make" produces more.

**R17.** Actions from a review get owners and dates, and are tracked like any other work. Reviews
whose actions are never done are a ritual.

**R18.** Where an incident revealed a standard was wrong, missing, or unfollowable, that is a finding
about the standard. Change it — `governance.standard-change`.

### Exceptions outside incidents

**R19.** A waiver requires: the requirement, the reason, the authority, an expiry, the compensating
control, and a review date (`GOVERNANCE.md` R29).

**R20.** Waivers do not inherit across releases, environments, or instances. Each is a fresh
decision.

**R21.** Repeated waiving of the same requirement MUST force a decision: fix the requirement, fix the
practice, or record why it does not apply. Perpetual waiving means the standard is wrong, the process
is wrong, or nobody is reading either.

---

## Evidence and acceptance

You can produce: the incident log with timelines and actions; the out-of-band escalation path and
when it was last tested; review documents with tracked actions; and the current waiver list with
expiries.

## Control points

| Control | Relevance here |
|---|---|
| `escalation.stop` | Always available, never requires authorization, never penalised |
| `recovery.rollback` | Protected even under incident pressure |
| `governance.waiver` | Expiry mandatory; no category crossing |

## Exceptions and stop conditions

**Stop** the incident response and escalate when: the action needed is protected and no authorizer is
reachable through any path; the response could cause worse harm than the incident; or you do not know
what the current state is. Acting on an unknown state during an incident is how one incident becomes
two.

## Anti-patterns

- Hesitating to declare because it might not be a real incident.
- Fixing first and capturing state never, so the postmortem has nothing to work with.
- Reconstructing the timeline afterwards from memory.
- Skipping authorization on a protected action because it was urgent.
- An expedited path anyone may invoke, invoked weekly.
- A review that names a person instead of a condition.
- Review actions with no owner, closed at quarter end.
- A waiver from an incident eighteen months ago, still in force.

## Templates

`templates/incident-report.md` · `templates/waiver.md`

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
