# Acceptance record — <task or deliverable>

> Template. Replace everything in angle brackets. Delete this line.
> Referenced by: `BS-CORE-WORK-INTAKE-AND-ACCEPTANCE` R11.
> **The executor does not fill this in for its own work** (R9).

| Field | Value |
|---|---|
| **Task ID** | `<identifier>` |
| **Deliverable class** | `<class>` |
| **Risk class** | `<class>` |
| **Required acceptance path** | `<from BS-DELEGATION-INDEPENDENT-REVIEW-AND-ACCEPTANCE R4>` |
| **Accepted by** | `<actor>` |
| **Date** | `<YYYY-MM-DD>` |
| **Outcome** | `<accepted \| partially accepted \| not accepted>` |

## Independence

| Test | Holds? | Note |
|---|---|---|
| Did not author the work | `<yes/no>` | |
| Does not share the author's session or take its assumptions as sole input | `<yes/no>` | |
| Cannot write to the evidence evaluated | `<yes/no>` | |
| Does not share the author's completion incentive | `<yes/no>` | |

<`independent` requires all four. `separate-actor` requires only the first — and must not be
described as independent review.>

## Criteria

| # | Criterion | Met | Evidence seen |
|---|---|---|---|
| 1 | `<criterion>` | `<yes/no>` | `<what the acceptor actually looked at — not an assertion>` |
| 2 | `<criterion>` | `<yes/no>` | |

## Findings

| Finding | Disposition | Owner | Date |
|---|---|---|---|
| `<what was found>` | `<fixed \| accepted-as-is + reason \| deferred>` | `<who>` | `<when>` |

<A finding with no disposition is unresolved, not closed.>

## If partially accepted

**Met:** <which criteria>
**Not met:** <which criteria>
**Disposition of the remainder:** <new task ID, or the decision made about it>

<"Accepted with follow-ups" without naming them is not acceptance.>

## Notes

<Anything the next reader needs. Disagreements and their resolution belong here, with who resolved
them.>
