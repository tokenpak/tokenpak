# Incident report — <short title>

> Template. Replace everything in angle brackets. Delete this line.
> Referenced by: `BS-CONTINUITY-INCIDENTS-AND-ESCALATION` R15.
> Sections 1–3 are filled **during** the incident, not after.

| Field | Value |
|---|---|
| **ID** | `<identifier>` |
| **Declared** | `<YYYY-MM-DD HH:MM + timezone>` |
| **Declared by** | `<actor>` |
| **Coordinator** | `<a person>` |
| **Severity** | `<your scale>` |
| **Resolved** | `<timestamp>` |
| **Status** | `<active \| stabilised \| resolved \| reviewed>` |

## 1. Impact

<Who is affected and how. In their terms, not in component terms — "customers cannot complete
checkout", not "queue service degraded".>

## 2. Timeline

> Written as it happens. Reconstructed timelines are already wrong by the time they are written.

| Time | Actor | Action or observation | Notes |
|---|---|---|---|
| `<HH:MM>` | `<who>` | `<what>` | |

## 3. Protected actions taken

| Time | Control | Action | Authorized by |
|---|---|---|---|
| `<HH:MM>` | `<control ID>` | `<what>` | `<who>` |

<Incidents do not suspend protected actions (`BS-CONTINUITY-INCIDENTS-AND-ESCALATION` R9). If a
break-glass path was used, record it here — that is what makes it break-glass rather than a bypass.>

## 4. What was deferred

| Deferred | Why | Owner | Due |
|---|---|---|---|
| `<step or evidence>` | | `<who>` | `<when>` |

## 5. Cause

<What actually happened. Conditions, not individuals — "the check was advisory and displayed like a
blocking one", not "X merged without checking".>

## 6. What made it worse, what made it better

**Worse:** <what slowed detection or response>
**Better:** <what worked — worth keeping deliberately>

## 7. How it got through

<Which gate, review, or control should have caught this, and why it did not. This is the section that
prevents recurrence, and the one most often replaced by "we'll be more careful".>

## 8. Actions

| Action | Owner | Due | Tracked as |
|---|---|---|---|
| `<change>` | `<who>` | `<when>` | `<task ID>` |

<Actions without owners and dates are a wish list. Track them like any other work.>

## 9. Standards findings

<Did this reveal a standard that was wrong, missing, or unfollowable? If so, that is a
`governance.standard-change`, not a note.>
