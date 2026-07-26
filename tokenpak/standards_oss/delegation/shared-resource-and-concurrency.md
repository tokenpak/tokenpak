---
id: BS-DELEGATION-SHARED-RESOURCE-AND-CONCURRENCY
layer: delegation
risk_class: high
default_coverage_profiles: [delegated-work, product-delivery, multi-agent]
control_points: [integrate.shared-baseline, work.scope-change]
---

# Shared resources and concurrency

## Purpose

Keep several actors working at once without corrupting shared state, overwriting each other, or
producing a result nobody authorized.

## Applies to

Any work where more than one actor can modify the same thing. **Read now if you have concurrent
executors** — and note that "one agent plus you" is already two.

---

## Requirements

### One lane per shared surface

**R1.** Each shared surface — a mainline branch, a published channel, a production configuration, a
shared document, a customer record — has **one integration lane**. Work enters through it, one change
at a time.

**R2.** The lane holder is a **coordination role, not an authority role**. Holding the lane means you
are the one integrating right now. It does not mean you may approve what you are integrating. These
are separate dials and conflating them is how unreviewed work reaches shared state under time
pressure.

**R3.** Before integrating, **re-verify that the base is current**. State that was true when the work
started is a hypothesis at integration time.

**R4.** Where integration is not atomic, the gap between check and act MUST be closed by re-checking
immediately before the act — not only at the start of the sequence. Anything that can change between
your check and your action will, eventually, at the worst time.

### Exclusive operations

**R5.** Operations that must not overlap — a release, a migration, a bulk update, a restore — MUST
hold an **exclusive lease** for their duration.

**R6.** A lease MUST have: a holder, a start time, an expiry, and a defined behaviour on expiry.
Leases without expiry become permanent locks held by processes that died.

**R7.** Lease handoff MUST be explicit. An expired lease is **not** an invitation to proceed; it is a
condition to investigate. The previous holder may still be mid-operation.

**R8.** On acquiring a lease, **re-check the preconditions** that made the operation appropriate.
They were evaluated before you waited.

### Isolation

**R9.** Concurrent actors SHOULD work in isolated copies and integrate through the lane, rather than
editing shared state directly. Isolation converts a race into a merge, and merges can be reviewed.

**R10.** Where isolation is impossible, the surface MUST have a single writer at a time, by lease.

**R11.** Each actor's write scope SHOULD be bounded to what its task requires. Broad write authority
plus concurrency produces changes nobody intended and nobody can attribute.

**R12.** Commits or submissions SHOULD be **scope-tight**: only the paths the work touched. Sweeping
up unrelated changes attributes them to the wrong work and hides them from the right review.

### When something goes missing

**R13.** Apparent loss of work MUST trigger a **search before a declaration**. Check for uncommitted
state, stashes, other copies, other actors' workspaces, and backups. Most "lost" work is misplaced.

**R14.** Recovery is **forward-only by default**. Reconstruct into current state rather than reverting
shared state to recover one actor's work — reverting shared state to recover one actor's work
destroys everyone else's.

**R15.** Destructive cleanup of shared state is a protected action. Preserve first, delete after
confirmation.

### Bypasses

**R16.** Where a bypass valve exists — skipping a check, forcing an update, overriding a lock — it
MUST be time-limited, recorded at the time, and attributed to an actor and a reason.

**R17.** A bypass MUST NOT be the routine path. Bypass frequency is a metric: if it is used weekly,
either the check is wrong or the process is, and one of them needs fixing.

---

## Evidence and acceptance

At any moment you can answer: who holds each lane, what exclusive operations are in flight, when
their leases expire, and which bypasses were used this period and by whom.

## Control points

| Control | Relevance here |
|---|---|
| `integrate.shared-baseline` | The lane, and re-verification before entry |
| `work.scope-change` | When an actor needs to touch outside its bounded write scope |

## Exceptions and stop conditions

**Stop** when: a lease is held by an unknown or unresponsive holder; the base changed between
verification and integration; two actors believe they hold the same lane; or work appears lost and
has not yet been searched for.

## Anti-patterns

- Two actors integrating into the same surface because neither checked.
- Lane holder treating lane possession as approval authority.
- Verifying the base at the start of a long sequence and acting at the end.
- Leases with no expiry, held by processes that exited weeks ago.
- Treating an expired lease as permission to proceed.
- Declaring work lost within a minute of not seeing it.
- Reverting shared state to recover one actor's work.
- A force-override used so often that nobody remembers it is an override.

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
