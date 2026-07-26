---
id: BS-CORE-TRUTH-AND-EVIDENCE
layer: core
risk_class: critical
default_coverage_profiles: [starter, delegated-work, product-delivery, multi-agent]
control_points: [work.accept, publish.external-revocable, publish.external-irreversible]
---

# Truth and evidence

## Purpose

Keep what you report to yourself and to others tied to what actually happened. Most operating
failures are not wrong actions — they are correct actions taken on the basis of a number, a status,
or a summary that was never true.

## Applies to

All work, all modes, always. **Read now.** This standard does not vary by authority profile: mode
changes who authorizes, never what is true.

---

## Requirements

### The five values, and why one of them is not zero

**R1.** A missing measurement MUST NOT be displayed, stored, or reported as zero, empty, success, or
"fine". Absence of evidence is its own value and MUST be distinguishable from evidence of absence.

**R2.** Where a value may be absent, the distinction MUST be preserved through to whoever reads it:

| Value | Means |
|---|---|
| `measured-zero` | It was measured. It is zero. |
| `not-measured` | Nobody looked. |
| `unavailable` | Someone looked; the source did not answer. |
| `not-applicable` | The question does not apply here. |
| `unknown` | We do not know which of the above is the case. |

Collapsing these into one falsy value is the single most common way a system reports health it does
not have. A dashboard showing "0 errors" because the error feed is down is worse than one showing
nothing at all — it is confidently wrong.

**R3.** Aggregations MUST NOT silently absorb absent inputs. A total computed over partial data MUST
be labelled as partial, or MUST NOT be shown.

### Reporting your own outcomes

**R4.** Report what happened, not what was attempted. These are different states and MUST NOT be
conflated:

```
proposed  →  submitted  →  executed  →  verified  →  accepted
```

Saying "done" when you mean "submitted", or "verified" when you mean "no error was raised", is a
false statement regardless of intent.

**R5.** If part of the work failed, was skipped, or was not attempted, that MUST be stated
explicitly in the same report as the successes — not omitted, and not deferred to a follow-up.

**R6.** Where something was not verified, say so. "I did not check X" is a complete and acceptable
report. "X is fine" when X was not checked is not.

**R7.** An error MUST be surfaced with what was being attempted, what failed, and what the reader can
do about it. Errors that say only that something went wrong impose the diagnosis on the reader.

### Claims and confidence

**R8.** A claim about performance, cost, savings, reliability, security, privacy, or capacity MUST
have an identifiable basis: what was measured, under what conditions, when. A claim without a basis
MUST NOT be made, internally or externally.

**R9.** Measured results SHOULD be reported as ranges with their conditions, not as single headline
numbers. A single number implies a precision that a measurement with variance does not have.

**R10.** Results MUST NOT be selected after the fact to support a conclusion. If you ran it five
times, report five, or state the selection rule you set in advance.

**R11.** Confidence MUST be expressed proportionally to evidence. "I believe", "I verified", and "I
assume" are different statements and MUST NOT be used interchangeably.

### Corrections

**R12.** When something you reported turns out to be wrong, correct it where it was said, promptly,
stating what was wrong and what is now believed.

**R13.** **Correct forward; do not quietly rewrite.** Amending the original record so the error never
appears to have happened destroys the reasoning trail and is governed by `record.history-alter`
(a protected action).

**R14.** Discovering that a previous report was wrong MUST trigger a check of whether anything was
decided on the basis of it.

---

## Evidence and acceptance

| You are asserting | Minimum evidence |
|---|---|
| A task is complete | The acceptance criteria, and the observation that each is met |
| A number | The measurement, its conditions, its date |
| A system is healthy | The check that ran, and when — not the absence of complaints |
| A risk is handled | The specific control, and evidence it is active |
| Nothing changed | The comparison you performed |

## Control points

| Control | Relevance here |
|---|---|
| `work.accept` | Acceptance requires evidence proportional to the deliverable class |
| `publish.external-revocable` | Claims must be substantiated before publication |
| `publish.external-irreversible` | Same, and the substantiation is itself part of the record |

## Exceptions and stop conditions

**Stop and escalate** when you would have to assert something you cannot support — including when a
measurement you depend on has failed. Reporting `unavailable` and stopping is always correct.
Guessing to avoid an awkward status is never correct.

There is no exception path for R1–R3. A system that fabricates a value under time pressure will
fabricate one under normal conditions too.

## Anti-patterns

- Rendering a failed lookup as `0`, `—`, or a green check.
- "Tests pass" when the suite did not run.
- Reporting the mean of a run set chosen after seeing the results.
- Burying a partial failure below a summary that reads as success.
- Reporting progress by intention: "starting X" recorded as "X done".
- Amending yesterday's status so today's looks consistent.
- Treating the absence of an alert as evidence that the alerting works.

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
