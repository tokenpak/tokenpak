---
id: BS-CORE-RECORDS-AND-AUDITABILITY
layer: core
risk_class: high
default_coverage_profiles: [starter, delegated-work, product-delivery, multi-agent]
control_points: [record.history-alter, governance.waiver, governance.standard-change]
---

# Records, provenance, and auditability

## Purpose

Make decisions and consequential actions recoverable after the fact — by you, by whoever inherits
this work, and by whoever has to explain it. Records are the only thing that survives context.

## Applies to

All work, all modes. **Configure now** — decide where records live before you need one.

Records get *more* important as autonomy increases, not less. When you approve everything
personally, your memory is a partial backup. When work proceeds without you, the record is the only
account of why anything happened.

---

## Requirements

### What must be recorded

**R1.** Record every **material decision**: a choice that closes off alternatives, commits
resources, or would be expensive to reverse. Not every choice — material ones.

**R2.** Record every **risk-bearing action**: anything protected, anything irreversible, anything
touching money, credentials, or other people's data.

**R3.** A decision record MUST contain: what was decided, what alternatives were considered, why,
who decided, when, and what would change the decision. The last field is the one that makes the
record useful later and the one most often omitted.

**R4.** Record decisions **when they are made**, not when they are questioned. Reconstructed
reasoning is a description of the outcome, not of the decision.

**R5.** Where authority was exercised, the record MUST name the actor and the basis — the control,
the envelope, or the delegation instrument.

### How records behave

**R6.** Records are **append-only by default**. Corrections are appended, not overwritten. Editing
an existing record is governed by `record.history-alter`, a protected action.

**R7.** **Supersede; do not delete.** A superseded record stays readable, marked superseded, pointing
to what replaced it.

**R8.** The record of a piece of work MUST be created **before** the work begins where the work is
consequential. A log opened at the end is a summary, and summaries omit exactly what you will want.

**R9.** State is **not inferred**. A milestone is reached when someone or something records that it
was reached, not because a later step started. Inferring "verified" from "no error" is the same
defect as inferring `0` from a failed measurement.

### Conflicting records

**R10.** When two records conflict, the governing one is the **most recent correct authorized**
record — all three conditions:

- **Recent** — later in time.
- **Correct** — consistent with the standards in force.
- **Authorized** — made by an actor with authority for that decision.

**R11.** Newer is not automatically governing. A later record that reverses an earlier decision
without authority is a **regression to be flagged**, not an instruction to be followed.

**R12.** **Failing to look for the prior record is itself the violation.** "I did not know it had
been decided" is not a defence where a lookup was available. The obligation is to check, not to
remember.

### Evidence and receipts

**R13.** Where a check gates an action, retain a **receipt**: what ran, when, against what, and what
it found. "The check passed" without a receipt is an assertion, not evidence.

**R14.** Receipts SHOULD reference rather than embed. Store a hash, an identifier, or a pointer;
copying sensitive payloads into a log turns your audit trail into a second place your secrets live.

**R15.** Records MUST have a stated **retention period** and a disposal path. An unbounded record
store is an unbounded liability.

**R16.** Access to records MUST be controlled proportionally to their content, and freshness MUST be
visible — an undated record cannot be relied on.

### Scope discipline

**R17.** Record scope is proportional to consequence. Recording everything produces a store nobody
reads, which is functionally the same as recording nothing while costing more.

---

## Evidence and acceptance

A record set is adequate when someone who was not present can answer, without asking you: what was
decided, by whom, on what basis, what was done, whether it worked, and what is still open.

Test it deliberately. Pick a decision from a month ago and try to answer those six questions from the
records alone.

## Control points

| Control | Relevance here |
|---|---|
| `record.history-alter` | Protected. Correct forward by default; alteration is the exception |
| `governance.waiver` | Every waiver is a record with a mandatory expiry |
| `governance.standard-change` | Changes to the standards are themselves recorded decisions |

## Exceptions and stop conditions

**Stop** when you are about to take a consequential action that you cannot record — no log, no
identity, no timestamp. An unrecordable consequential action is an accountability gap, and the right
response is to fix the gap rather than to proceed and reconstruct.

Expedited paths (incidents) MAY defer the *full* record, and MUST capture at minimum: what was done,
by whom, when, and why — at the time. The remainder is repaid within the stated window.

## Anti-patterns

- Writing the decision record after someone challenges the decision.
- A status inferred from a downstream step having started.
- Overwriting yesterday's entry so the sequence reads cleanly.
- A "temporary" waiver with no expiry, still in force a year later.
- Logs containing the credentials they were meant to prove were rotated.
- Recording every automated action at the same weight as a decision, then reading none of them.
- Two actors keeping separate private records of the same shared work.

## Templates

`templates/decision-record.md` · `templates/acceptance-record.md` · `templates/waiver.md`

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
