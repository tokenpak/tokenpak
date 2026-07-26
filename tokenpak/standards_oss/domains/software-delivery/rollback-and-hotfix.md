---
id: BS-SWDEL-ROLLBACK-AND-HOTFIX
layer: domains/software-delivery
risk_class: critical
default_coverage_profiles: [product-delivery, multi-agent]
control_points: [recovery.rollback, publish.external-irreversible, record.history-alter, governance.waiver]
---

# Rollback and hotfix

## Purpose

Respond to a bad release without making it worse.

## Applies to

Any released software. **Read now** — before you need it.

---

## Requirements

### Prefer forward

**R1.** **Supersede rather than withdraw.** Publishing a corrected version usually serves users better
than removing what they already installed, and it preserves the record.

**R2.** Withdrawal is for **user harm**: data loss, security exposure, a defect that damages the
people who installed it. Not for embarrassment, and not for a defect that a new version fixes.

**R3.** Withdrawing a published release is `recovery.rollback` — protected. It requires authorization
even under pressure.

**R4.** Deleting or repointing a published version marker is `record.history-alter` — protected, and
almost never right. Users who already fetched it now hold something that no longer matches its name,
and they will not find out until it fails.

**R5.** Assume you cannot recall what was published. Mirrors, caches, lock files, and offline copies
persist. Plan on the basis that the bad version is still out there.

### Deciding

**R6.** The decision ladder scales with harm:

| Situation | Decider |
|---|---|
| Cosmetic or minor defect | Normal process; fix in the next release |
| Functional defect, workaround exists | Release owner; hotfix decision |
| Broad functional failure | Release owner with a recorded go |
| Data loss, security exposure, or user harm | Operator; incident procedure |

**R7.** Decide against **impact on users**, not against how bad it looks. Those diverge, and the
second is a poor guide.

**R8.** Record the decision as it is made — what was known, what was decided, by whom. This is the
record you will most want later and are least likely to write.

### Hotfix

**R9.** A hotfix is **expedited, not exempt**. It may compress review, reorder stages, and defer
non-blocking evidence. It MUST NOT skip authorization for the publish step, and MUST NOT lower
correctness.

**R10.** A hotfix is **minimal**: the smallest change that addresses the harm. Unrelated fixes riding
along turn a scoped urgent change into an unreviewed release.

**R11.** A hotfix still carries a regression test for the defect it fixes
(`verification-and-testing-evidence.md` R2). Under urgency this is more valuable, not less — hotfixes
are disproportionately likely to be re-broken.

**R12.** What was deferred is recorded **at the time** and repaid within a stated window. Track the
debt like any other work; unpaid hotfix debt is how the next incident starts.

**R13.** A hotfix branch merges back to the integration line. A fix that ships but never lands in
mainline is reintroduced by the next release.

### Recovering

**R14.** Before reverting, verify the **target state is actually good**. Reverting to a state you have
not checked replaces a known problem with an unknown one.

**R15.** Consider dependents. Others may have already adapted to the new behaviour; reverting breaks
them second.

**R16.** Tell affected users what happened, what to do, and by when — in the place they would look,
not only where it is convenient to post.

**R17.** After stabilising, run the incident review (`BS-CONTINUITY-INCIDENTS-AND-ESCALATION` R15).
Include how the defect passed the gates: that is the finding that prevents a recurrence, and it is
usually a gate that was advisory when everyone thought it was blocking.

---

## Evidence and acceptance

For any hotfix or withdrawal you can produce: the decision and decider, what was deferred and whether
it was repaid, the user communication, the regression test, and the review finding about how it got
through.

## Control points

| Control | Category |
|---|---|
| `recovery.rollback` | Protected — human-authorized |
| `publish.external-irreversible` | Protected — the hotfix release itself |
| `record.history-alter` | Protected — deleting or repointing a version marker |
| `governance.waiver` | What the expedited path deferred |

## Exceptions and stop conditions

**Stop** when: the target state for a revert is unverified; a withdrawal is proposed for a
non-harm reason; nobody with authority to authorize the publish is reachable; or the hotfix has grown
beyond the minimal change.

There is no expedited path around the publish authorization. Urgency is when improvisation is most
tempting and most costly.

## Anti-patterns

- Deleting a published version marker to "clean up" a bad release.
- Withdrawing because the release was embarrassing.
- A hotfix that also includes three unrelated fixes.
- Skipping the regression test because it is urgent.
- Deferred evidence never recorded, so never repaid.
- A hotfix that never merges back, reintroduced next release.
- Reverting to a version nobody re-verified.
- No user communication because the fix was fast.

## Templates

`templates/incident-report.md` · `templates/release-log.md` · `templates/waiver.md`

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
