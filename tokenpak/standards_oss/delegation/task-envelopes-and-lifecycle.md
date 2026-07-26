---
id: BS-DELEGATION-TASK-ENVELOPES-AND-LIFECYCLE
layer: delegation
risk_class: high
default_coverage_profiles: [delegated-work, product-delivery, multi-agent]
control_points: [work.accept, work.scope-change, spend.paid-action]
---

# Task envelopes and lifecycle

## Purpose

Give a delegated unit of work a shape it cannot silently exceed: what it may do, what it must not do,
what states it can be in, and who may move it between them.

## Applies to

Any work handed to another actor, especially an autonomous one. **Read now if agents do the work.**

---

## Requirements

### The envelope

**R1.** Every delegated task MUST carry an envelope stating:

| Field | Why it exists |
|---|---|
| Outcome | What "done" means, in one sentence |
| Acceptance criteria | How done is judged, written before the work |
| Allowed actions | What this task may do |
| Forbidden actions | What this task must not do, even if it seems helpful |
| Resource limit | Time, spend, calls — with a unit |
| Stop condition | What makes this task halt rather than continue |
| Escalation target | Who to reach, and what happens if they do not answer |

**R2.** **Forbidden actions are stated explicitly, not inferred** from the allowed list. An executor
reasoning about what is implicitly permitted will reason its way outward, especially when the
outward action looks helpful.

**R3.** Actions that cost money, touch external parties, or modify shared state are **off by
default** and MUST be enabled per task. Default-off is the design; a task that needs them says so.

**R4.** An envelope without a resource limit is incomplete. Unbounded work cannot fail — it can only
be noticed and killed.

**R5.** A task MUST NOT widen its own envelope. Hitting the edge is a `work.scope-change` requiring
the authority named for it.

### Lifecycle

**R6.** Task states are a **closed set**. Ad-hoc states are not permitted — a status nobody else
understands is not a status.

```
proposed → ready → in-progress → submitted → accepted → closed
                              ↘ blocked ↗
                              ↘ stopped → reviewed
```

**R7.** Transition authority is explicit:

| Transition | Who may make it |
|---|---|
| `proposed → ready` | Whoever may authorize the work |
| `ready → in-progress` | The executor |
| `in-progress → submitted` | The executor |
| `submitted → accepted` | **Never the executor** (`work.accept`) |
| `accepted → closed` | Whoever accepted, or an owner |
| any → `blocked` / `stopped` | Any actor, always |

**R8.** **An executor never sets a terminal state on its own work.** `accepted` and `closed` are not
available to whoever did the work, in any authority profile.

**R9.** `blocked` MUST record what would unblock it and who was asked. A block with no named
dependency is an abandonment wearing a status.

**R10.** Every task carries an owner who is a **person**, not a queue or a role. Queues do not answer
questions.

### Sessions and continuity

**R11.** A working session is bounded and explicit. When continuity is uncertain, **fail closed to a
fresh start** rather than assuming inherited context.

**R12.** A resumed session MUST re-run its preflight checks. State that was true when the session
paused is a hypothesis when it resumes, not a fact.

**R13.** Context carried between sessions MUST be explicit and inspectable. Implicit carry-over is
how an agent acts on a stale assumption while reporting confidently.

**R14.** A task interrupted mid-flight MUST leave a recoverable state and a record of where it
stopped. Where a partial effect cannot be left safely, the task MUST either complete the unit or
reverse it — never leave it half-applied silently.

### Volume

**R15.** Mechanical, low-risk, high-volume transitions MAY follow a fast path with batched review,
provided the risk tier is declared, the batch is recorded, and terminal-state authority is unchanged.

**R16.** A fast path MUST have an **auto-suspend**: an anomaly rate or volume threshold above which
it stops and asks. A fast path without one is a way to make a mistake at scale.

---

## Evidence and acceptance

A well-formed task can be read by an actor who was not present and executed without further
questions — or it stops immediately and says exactly what is missing. Both outcomes are successes.

## Control points

| Control | Relevance here |
|---|---|
| `work.accept` | Terminal-state authority; never the executor |
| `work.scope-change` | The envelope edge |
| `spend.paid-action` | The resource limit, and what happens at it |

## Exceptions and stop conditions

**Stop** when: the envelope is missing a field; a required action is not in the allowed list; the
resource limit is reached; the stop condition is met; or a protected action is required and not
authorized.

Reaching a limit is a **successful stop**, not a failure. Report it as such — an executor that treats
its limit as an obstacle to route around has no limit.

## Anti-patterns

- Tasks whose only instruction is a goal, with no bounds.
- Forbidden actions left implicit because "it obviously wouldn't do that".
- An executor marking its own work accepted.
- `blocked` with no named dependency, sitting for weeks.
- Inheriting session context silently and acting on a stale assumption.
- A fast path with no anomaly threshold.
- Statuses invented per task, so no two tasks can be compared.

## Templates

`templates/task.md` · `templates/acceptance-record.md`

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
