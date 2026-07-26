---
id: BS-SWDEL-RELEASE-PIPELINE-AND-LOG
layer: domains/software-delivery
risk_class: critical
default_coverage_profiles: [product-delivery, multi-agent]
control_points:
  [publish.external-irreversible, publish.external-revocable, governance.waiver, integrate.shared-baseline]
---

# Release pipeline and log

## Purpose

Ship deliberately: a known sequence, gates that mean something, and a record written as it happens.

## Applies to

Any release reaching people outside the project. **Read now if you publish releases.**

---

## Requirements

### The sequence

**R1.** Releases follow a declared sequence. Improvising release steps is where the expensive,
irreversible mistakes live.

```
1. Scope frozen            what is in this release; changes after this are a decision
2. Verified                evidence complete for every change in scope
3. Staged                  built and validated as the artifact that will ship
4. Go / no-go              a decision by a named authority, recorded
5. Published               the irreversible step
6. Confirmed               observed working from outside
7. Announced               claims substantiated
8. Closed out              log completed, debts recorded, retrospective written
```

**R2.** Stages are not skipped. They may be compressed — see `rollback-and-hotfix.md` for the
expedited path, which reorders and defers but does not skip authorization.

**R3.** **Step 5 is `publish.external-irreversible`** — protected in every authority profile. It
requires fresh authorization naming the exact artifact identity (`BS-CORE-RISK-AND-PROTECTED-ACTIONS`
R12).

**R4.** Between authorization and publication, re-verify that the artifact is unchanged. A material
change invalidates the authorization.

**R5.** Publish from a **verified artifact**, not from a rebuild. If you rebuild, you are shipping
something nothing verified.

### Go / no-go

**R6.** The go decision answers, explicitly and out loud:

- Did every gate pass, and did the ones described as required actually run?
- What is not verified?
- What are the open waivers, and do they expire before or after this release?
- If this is wrong, how do we find out, and how fast can we respond?
- Who is available afterwards?

**R7.** Go is given by a **named authority**, recorded, at a moment. Not inferred from nobody
objecting.

**R8.** Any participant may say no-go. Overriding a no-go is a decision with a record and a named
authority.

**R9.** Absence of a signal is not a pass (`BS-CORE-TRUTH-AND-EVIDENCE` R1). A gate that did not
report has not passed.

### The log

**R10.** The release log is created **before** the release starts and is **append-only**.

**R11.** Each entry: what was done, by whom, when, the outcome, and where the evidence is.

**R12.** Milestones are recorded when reached, not inferred from a later step having started
(`BS-CORE-RECORDS-AND-AUDITABILITY` R9).

**R13.** The log records what did **not** happen: skipped checks, deferred evidence, waivers used,
things noticed and not fixed.

**R14.** After release, a short retrospective — what went well, what did not, what changes next time.
Three lines is enough; zero lines means the next release repeats this one.

### Gates

**R15.** Each gate declares whether it is **blocking or advisory**, and is reported that way. Advisory
results shown identically to blocking ones train everyone to ignore both.

**R16.** A gate described as required MUST run and MUST block. A required gate with nothing enforcing
it is a false assurance (`GOVERNANCE.md` R24).

**R17.** Waiving a gate requires a waiver with an expiry (`governance.waiver`). Waivers do not
inherit to the next release.

**R18.** **Count gate waivers.** Repeated waiving of the same gate blocks new feature work until it is
resolved — fix the gate, fix the practice, or record why it does not apply. Without a counter, waiving
becomes the process.

### After publishing

**R19.** Confirm from **outside**: install or fetch what a user would, from where they would get it,
and verify it works. Build-system success is not confirmation.

**R20.** Observe for a declared period before calling it done, with someone named as watching and a
stated threshold for acting.

**R21.** Closeout is explicit. An unclosed release leaves the next one starting from an unknown state.

---

## Evidence and acceptance

For any release you can produce: the log, the go decision and who made it, the artifact identity
published, the external confirmation, the waivers used with expiries, and the retrospective.

## Control points

| Control | Relevance here |
|---|---|
| `publish.external-irreversible` | Protected. Step 5, per artifact identity |
| `publish.external-revocable` | Announcements and release notes |
| `governance.waiver` | Gate waivers, with expiry, counted |
| `integrate.shared-baseline` | Scope freeze and what enters it |

## Exceptions and stop conditions

**Stop** when: a gate did not report; the artifact changed after go; the publishing credential or
identity is not what you expect; nobody is available to watch afterwards; or the go decision cannot
name what is unverified.

**Never improvise a release step.** Ambiguity about tagging, publishing, identity, or credentials
stops the release and escalates. The cost of a delayed release is bounded; the cost of a wrong
irreversible publication is not.

## Anti-patterns

- Publishing from a rebuild "to be safe".
- Go inferred from silence on a call.
- A log written after the release, from memory.
- A milestone marked done because the next step started.
- Advisory and blocking checks displayed identically.
- A waiver from the last release still applied to this one.
- Confirming success by looking at the build system.
- No retrospective, and the same failure next time.

## Templates

`templates/release-log.md` · `templates/validation-checklist.md` · `templates/waiver.md`

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
