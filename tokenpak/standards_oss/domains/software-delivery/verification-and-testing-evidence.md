---
id: BS-SWDEL-VERIFICATION-AND-TESTING-EVIDENCE
layer: domains/software-delivery
risk_class: high
default_coverage_profiles: [product-delivery, multi-agent]
control_points: [work.accept, integrate.shared-baseline]
---

# Verification and testing evidence

## Purpose

Define what evidence a software change needs before anyone can honestly say it works.

## Applies to

Any change to shipped software. **Read now if you write code.** This is the domain instance of
`BS-CORE-WORK-INTAKE-AND-ACCEPTANCE` R6 — the evidence column, filled in for code.

---

## Requirements

### Evidence by change class

**R1.** Evidence attaches to the change class, declared before the work:

| Change class | Minimum evidence |
|---|---|
| New behaviour | Tests covering the intended behaviour and its boundaries; the failure paths exercised |
| Bug fix | **A test that fails before the fix and passes after** |
| Refactor | Existing tests pass unchanged; no behaviour change claimed or made |
| Performance change | Before and after measurement, same conditions, variance stated |
| Dependency change | Existing suite passes; changelog reviewed for behaviour changes |
| Configuration or infrastructure | The change applied to a real target and observed |
| Documentation | Examples executed as written |

**R2.** **Every bug fix carries a regression test.** Without one you have evidence that it works now,
and none that it will keep working. This is the single highest-value rule in this document.

**R3.** A test that has never failed has not been shown to test anything. Verify new tests fail
against the unfixed code.

**R4.** Evidence bars are not lowered for time. Reduce scope, not assurance
(`BS-CORE-WORK-INTAKE-AND-ACCEPTANCE` R7).

### The suite

**R5.** Weight the suite toward fast, isolated tests, with fewer integrated ones, and a small number
covering the paths that actually matter end to end. Inverting this produces a suite too slow to run
and too flaky to believe.

**R6.** Tests are **deterministic**. A test that fails intermittently is a defect in the test or the
system, and it trains everyone to ignore failures — which is worse than not having it.

**R7.** A persistently flaky test is fixed, or removed with a recorded decision. It is never left
failing intermittently in a suite people are expected to trust.

**R8.** Tests are independent of order and of each other. Shared mutable state between tests produces
failures that depend on what else ran.

**R9.** Tests do not reach external services or incur cost unless explicitly marked and separately
run. A suite that pays per invocation will stop being run.

**R10.** Coverage is a **diagnostic, not a target**. Coverage as a gate produces tests written to
raise a number.

### Trusting the result

**R11.** "The suite passed" requires that the suite **ran**, over the change in question. A skipped,
cached, or filtered run is not a pass — see `BS-CORE-TRUTH-AND-EVIDENCE` R1.

**R12.** Retain the receipt: what ran, against which revision, when, with what result.

**R13.** Verification runs against the **artifact you will ship**, not only against source. What is
built and packaged can differ from what is tested.

**R14.** A check that is described as required MUST actually run and MUST actually block. A check
labelled required with nothing enforcing it is worse than an acknowledged gap — it converts a known
risk into a false assurance (`GOVERNANCE.md` R24).

**R15.** Where a check is advisory, say so where it is reported. Advisory checks reported identically
to blocking ones teach people to ignore both.

**R16.** A check disabled to unblock work is a **waiver**: recorded, with an expiry and an owner
(`GOVERNANCE.md` section 9).

---

## Evidence and acceptance

For any change reaching the shared line, you can produce the change class, the evidence that class
requires, and the receipt showing it ran against that revision.

## Control points

| Control | Relevance here |
|---|---|
| `work.accept` | Acceptance requires the evidence for the declared class |
| `integrate.shared-baseline` | Verification evidence before entry |

## Exceptions and stop conditions

**Stop** when the required evidence cannot be produced. The correct responses are to reduce scope, fix
the obstacle, or record a waiver with an expiry. Proceeding while describing unverified work as
verified is a false statement, not a shortcut.

## Anti-patterns

- A fix with no test, because the fix was obvious.
- A new test that passes against the unfixed code.
- A flaky test everyone re-runs until it goes green.
- Coverage targets met by tests that assert nothing.
- "Suite passed" from a cached result for a different revision.
- Testing source while shipping a differently-built artifact.
- A required check with no enforcing mechanism.
- A check disabled "temporarily" with no expiry.

## Templates

`templates/validation-checklist.md`

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
