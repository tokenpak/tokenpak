---
id: BS-SWDEL-INTEGRATION-AND-MERGE-CONTROL
layer: domains/software-delivery
risk_class: high
default_coverage_profiles: [product-delivery, multi-agent]
control_points: [integrate.shared-baseline, access.grant-escalation, record.history-alter]
---

# Integration and merge control

## Purpose

Get changes into the shared line without breaking it, losing them, or losing track of who approved
what.

## Applies to

Any project with a shared branch more than one actor writes to. **Read now.**

---

## Requirements

### Branch shape

**R1.** One branch is the integration line. It is always in a state you could release from, or you
know precisely why not.

**R2.** Work branches are short-lived. A long-lived branch is a merge problem accruing interest, and
it is paid at the worst time.

**R3.** Branch names carry their purpose and their owner. A branch nobody can attribute is a branch
nobody will delete.

**R4.** Protections on the integration line are declared: who may push, what must pass, what approval
is needed.

**R5.** **Weakening a protection is protected** (`access.grant-escalation` via
`software.weaken-branch-protection`): authorized, time-bounded, recorded, and restored. "Temporarily
disabled" without an expiry is permanently disabled.

### Review before integration

**R6.** A change is reviewed by someone who did not author it, per the independence requirement for
`integrate.shared-baseline` in the active authority profile.

**R7.** Changes are sized for review. A change nobody can hold in their head gets approved rather than
reviewed, and everyone knows it.

**R8.** **Re-verify the base immediately before merging.** Approval was against a base that has since
moved. Anything that can change between check and merge will, eventually.

**R9.** Where required checks did not run — infrastructure down, quota exhausted — merging is a
decision, not a default. Record what was not verified and what compensating evidence was used
(running the suite locally, for instance), and repay the gap.

**R10.** Compensating controls are named at the time, not reconstructed later.

### Merge mechanics

**R11.** The merge method is a **declared, consistent choice**. Mixed methods make history unreadable
and make reverting unreliable.

**R12.** Know what your merge method does to **authorship and attribution**. Some methods rewrite the
author, the committer, or both. If attribution matters — and it does for accountability — verify what
yours produces, once, deliberately.

**R13.** Merging does not rewrite history others have pulled. Rewriting published history is
`record.history-alter` — protected — and is rarely worth what it costs.

**R14.** The merge record retains: what was merged, who approved it, and against what evidence. A
squashed merge that discards the review trail loses the accountability that made the review worth
doing.

### Concurrency

**R15.** One integration at a time per line (`BS-DELEGATION-SHARED-RESOURCE-AND-CONCURRENCY` R1).

**R16.** Whoever holds the merge lane holds a **coordination role, not approval authority**. They may
be merging something they are not permitted to approve.

**R17.** A conflict is resolved by understanding both sides, not by preferring one wholesale. Taking
"ours" to clear a conflict silently discards someone's work.

**R18.** After resolving a conflict, **re-run verification**. A conflict resolution is new code that
nothing has yet tested.

---

## Evidence and acceptance

For any merge on the integration line you can name: the author, the reviewer, the evidence, the base
it was verified against, and any protection that was weakened, by whom, and when it was restored.

## Control points

| Control | Relevance here |
|---|---|
| `integrate.shared-baseline` | Review and re-verification before entry |
| `access.grant-escalation` | Weakening protections — bounded and restored |
| `record.history-alter` | Rewriting published history — protected |

## Exceptions and stop conditions

**Stop** when: the base moved after approval; required checks did not run and no compensating
evidence exists; the merge would rewrite published history; or two actors believe they hold the lane.

## Anti-patterns

- A branch open for four months, merged in a hurry.
- Protection disabled to unblock a release, never restored.
- Approving a change too large to review.
- Merging against a base from two days ago.
- A merge method that silently replaces the author, discovered during an audit.
- Resolving a conflict by taking one side wholesale.
- Merging a conflict resolution without re-running anything.

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
