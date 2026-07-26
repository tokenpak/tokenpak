---
id: BS-CONTINUITY-BACKUP-AND-RECOVERY
layer: continuity
risk_class: high
default_coverage_profiles: [delegated-work, product-delivery, multi-agent]
control_points: [data.destroy, recovery.rollback]
---

# Backup and recovery

## Purpose

Be able to get back what matters, in a time you have actually measured.

## Applies to

Any operator holding state whose loss would hurt — work product, records, configuration, client
material. **Configure now.**

---

## Requirements

### What and where

**R1.** Identify what would actually hurt to lose. Back that up. Backing up everything indiscriminately
produces a restore process too slow to use and a store too large to verify.

**R2.** For each backed-up item, state: how often it is captured, how long copies are kept, where they
live, and who can reach them.

**R3.** At least one copy MUST be **isolated** from the primary — different location, different
credentials, different failure mode. A backup reachable with the same credentials as the primary does
not survive a credential compromise, which is one of the failure modes you are backing up against.

**R4.** Backups containing confidential or restricted data carry the **same** protection obligations
as the primary. Backups are where forgotten copies of sensitive data live.

### Recovery is the requirement

**R5.** **An unrestored backup is a hypothesis, not a capability.** The requirement is a demonstrated
restore, not a completed capture.

**R6.** Restore MUST be rehearsed on a stated cadence, into a real target, verifying the restored data
is complete and usable — not merely that the restore command exited zero.

**R7.** Recovery targets — how long it takes, how much you lose — MUST come from the rehearsal, not
from intent. An untested target is a wish with a number attached.

**R8.** Where the rehearsed time is unacceptable, that is a finding requiring a decision: change the
approach or accept the exposure explicitly.

**R9.** The recovery procedure MUST be usable by someone who is not the person who built it, under
pressure, without access to the system being recovered. This includes not storing the only copy of the
procedure inside the thing that failed.

### Before destruction

**R10.** Before any destructive operation — disposal, migration, bulk change, restore-over — capture
the current state first, and confirm the capture is readable.

**R11.** Confirm the restore path **before** destroying, not after. Verifying a backup after deleting
the original is how you discover it was corrupt.

**R12.** Where no restore path exists, the loss MUST be explicitly accepted by whoever bears it,
recorded, before proceeding.

### Silence is not health

**R13.** Backup success MUST be **positively confirmed**. Absence of a failure alert is not evidence
of success — it is equally consistent with the backup job not running at all
(`BS-CORE-TRUTH-AND-EVIDENCE` R1).

**R14.** Verify the **content**, not only the job. A job that completes successfully while writing an
empty archive reports success.

**R15.** Backup coverage MUST be re-checked when the work changes shape. New data stores are not
backed up by the arrangement made for the old ones.

---

## Evidence and acceptance

You can state the date of the last **successful rehearsed restore**, the measured recovery time, and
what is not covered. If the last restore was never, the correct answer is "we have backups; we have
never demonstrated recovery" — say that rather than implying capability you have not shown.

## Control points

| Control | Relevance here |
|---|---|
| `data.destroy` | Protected. Restore path confirmed, or loss explicitly accepted |
| `recovery.rollback` | Protected. Target state verified good before reverting |

## Exceptions and stop conditions

**Stop** when: a destructive operation is proposed and the restore path is unconfirmed; a restore has
never been rehearsed and one is now needed; or backup success is inferred from silence.

## Anti-patterns

- Backups running for years, never restored.
- The restore runbook stored only on the system it recovers.
- Backup credentials identical to primary credentials.
- Recovery time quoted from the design document.
- "No alerts, so backups are fine."
- A successful job writing an empty archive.
- A new data store added without extending backup coverage.
- Deleting the original, then discovering the backup was partial.

## Templates

`templates/validation-checklist.md` (restore rehearsal) · `templates/incident-report.md`

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
