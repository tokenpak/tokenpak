# Operator brief — <actor name>

> Template. Replace everything in angle brackets. Delete this line.
> Give this to every actor that will do work here — a new person, a contractor, or an agent. If you
> cannot fill it in, the actor should not be working yet.

| Field | Value |
|---|---|
| **Actor** | `<name or identifier>` |
| **Type** | `<human \| agent>` |
| **Roles held** | `<operator \| delegate \| reviewer \| executor \| auditor>` |
| **Accountable owner** | `<person>` |
| **Effective from** | `<date>` |
| **Review date** | `<date>` |

## 1. Scope

**Works on:** <what>
**Does not work on:** <what — stated explicitly, not inferred from the above>

## 2. Access

| System | Level | Granted by | Expires |
|---|---|---|---|
| `<system>` | `<narrowest sufficient>` | `<who>` | `<when — permanent requires a recorded decision>` |

## 3. Authority

**May authorize:** <control IDs, or "none">
**Envelope:** <bounds on that authority>
**Expires:** <date>
**May not sub-delegate unless stated here:** <none, or what>

## 4. Budgets

| Resource | Soft | Hard | On measurement failure |
|---|---|---|---|
| `<resource>` | `<value + unit>` | `<value + unit>` | Stop cleanly |

## 5. Protected actions

**This actor must never execute:**

- <from your `non_delegable` list>

**This actor may execute only with fresh per-instance authorization:**

- <from your `human_authorized` list>

<For agents, prefer making these structurally unreachable over instructing against them. An
instruction is not a boundary.>

## 6. Escalation

| Field | Value |
|---|---|
| Contact | `<who>` |
| Method | `<how>` |
| Response window | `<duration>` |
| If no response | `<stop-cleanly \| proceed within: …>` |
| Out-of-band path | `<for when the normal path is what is broken>` |

## 7. Emergency stop

**How this actor is stopped:** <mechanism>
**Who can stop it:** <anyone, ideally>
**Last tested:** `<date — untested means unproven>`

## 8. What to read

| Order | Document |
|---|---|
| 1 | `START-HERE.md` section 2 and section 8 |
| 2 | `GOVERNANCE.md` section 3 and section 4 |
| 3 | The compiled effective profile for this work |
| 4 | <role-specific standards> |

## 9. Acknowledgement

| Field | Value |
|---|---|
| Read and understood by | `<actor or its operator>` |
| Date | |
