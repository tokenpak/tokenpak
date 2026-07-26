# Task — <short title>

> Template. Replace everything in angle brackets. Delete this line.
> Referenced by: `BS-DELEGATION-TASK-ENVELOPES-AND-LIFECYCLE` R1.
> An envelope missing any field below is incomplete — the executor should stop and ask.

| Field | Value |
|---|---|
| **ID** | `<stable identifier>` |
| **Owner** | `<a person — not a queue, not a role>` |
| **Executor** | `<who or what does the work>` |
| **Status** | `<proposed \| ready \| in-progress \| submitted \| accepted \| closed \| blocked \| stopped>` |
| **Deliverable class** | `<decision \| document-or-analysis \| recommendation \| code-or-config \| external-communication \| financial-action \| operational-change>` |
| **Risk class** | `<critical \| high \| moderate \| low \| trivial>` |
| **Acceptance path** | `<derived from risk class — see BS-DELEGATION-INDEPENDENT-REVIEW-AND-ACCEPTANCE R4>` |

## Outcome

<One sentence. What "done" means.>

## Acceptance criteria

<Written before the work starts, by someone other than the sole executor where consequential.>

1. <criterion>
2. <criterion>

## Envelope

**Allowed actions**

- <what this task may do>

**Forbidden actions**

- <what it must not do, even if it seems helpful — stated explicitly, never inferred>
- <paid actions, external contact, and shared-state changes are off unless listed above>

**Resource limit**

| Resource | Limit | On reaching it |
|---|---|---|
| `<time \| spend \| calls>` | `<value + unit>` | Stop cleanly and report |

**Stop conditions**

- Resource limit reached.
- A protected action is required and not authorized.
- A required declaration is `unknown`.
- <task-specific condition>

**Escalation**

| Field | Value |
|---|---|
| Contact | `<who>` |
| Method | `<how>` |
| Response window | `<duration>` |
| If no response | `<stop-cleanly \| proceed-within-envelope: …>` |

## Context

<What the executor needs to know. Links to prior decisions. Explicit — nothing assumed as inherited
session context.>

## Notes

<Appended by the executor as work proceeds. Append-only.>
