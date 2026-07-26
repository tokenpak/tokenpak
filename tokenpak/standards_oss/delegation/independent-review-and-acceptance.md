---
id: BS-DELEGATION-INDEPENDENT-REVIEW-AND-ACCEPTANCE
layer: delegation
risk_class: critical
default_coverage_profiles: [delegated-work, product-delivery, multi-agent]
control_points: [work.accept, integrate.shared-baseline]
---

# Independent review and acceptance

## Purpose

Ensure risk-bearing work is not accepted because the actor that produced it says it is fine.

## Applies to

Any work reviewed by anyone other than its author — which, above a risk threshold, is all of it.
**Read now.**

---

## Requirements

### Independence

**R1.** A reviewer is independent only if all four tests in `GOVERNANCE.md` section 3a hold: it did not
author the work; it does not share the author's session or take the author's assumptions as sole
input; it cannot write to the evidence it evaluates; and it does not share the author's incentive to
declare completion.

**R2.** Failing any one test means the review is not independent, whatever it is labelled. The most
common failure is the second: an agent reviewing work produced in the same session, with the same
context, reliably confirms it. It is not checking; it is agreeing with itself.

**R3.** Where a control requires `independent`, all four tests MUST hold. Where it requires
`separate-actor`, only the first must — a lighter bar for lower-risk work, and it MUST NOT be
described as independent review.

### Matching the path to the risk

**R4.** Acceptance paths are graded, and the path is chosen by risk class **before** the work
starts:

| Risk class | Acceptance path |
|---|---|
| Critical — irreversible, external, money, credentials, data | Independent review (all four tests), plus evidence retained |
| High — shared state, external visibility, hard to reverse | Independent review |
| Moderate — reversible within your control | Separate actor |
| Low — mechanical, verifiable by re-running | Automated check with a retained receipt |
| Trivial — no external effect, trivially reversible | Self-verified with a record |

**R5.** The path MUST NOT be downgraded because the work turned out to be small, or because time ran
short. Reduce scope, not assurance.

**R6.** **Trivial and low classes MUST NOT be self-assigned by the executor.** Whoever benefits from
the lighter path does not choose it. This is the loophole that swallows the table.

**R7.** Where risk class is ambiguous, the **higher** class applies until someone with authority
classifies it.

### Conducting a review

**R8.** Review is against the acceptance criteria written at intake — not against a general sense of
quality, and not against what was delivered.

**R9.** A reviewer MUST inspect evidence, not accept assertions. "The check passed" is an assertion;
the receipt is evidence.

**R10.** A reviewer MUST be able to reach "not accepted". A review that structurally cannot fail is
theatre — check that yours can, by looking at whether any has.

**R11.** Review findings MUST be recorded with their disposition: fixed, accepted as-is with reason,
or deferred with an owner and a date. A finding with no disposition is unresolved, not closed.

**R12.** Disagreement between reviewer and author MUST be resolved by someone with authority, and the
resolution recorded. It MUST NOT be resolved by whoever is more persistent.

**R13.** Dissent MUST be preserved. Where a reviewer objected and was overruled, the objection stays
in the record. It is the most useful thing in the file when the decision turns out badly.

### Verification diversity

**R14.** For decisions that are critical **and** depend on judgement rather than a checkable fact,
review SHOULD come from a differently-constituted reviewer — a different person, a different tool, or
a different model. Two instances of the same reasoning process are one reviewer, not two.

**R15.** This is a recommendation scaled by risk, not a universal requirement. Requiring it
everywhere makes routine work expensive and trains people to route around it.

**R16.** Where multiple reviewers are used, their **basis for agreeing** matters more than the count.
Three reviewers given the same summary have checked the summary.

### When no acceptor is available

**R19.** Where the required acceptance path cannot be satisfied by any available actor — most often
because the only candidate authored the work — the correct response is to **convene a separate
acceptance lane**. Self-accepting and stalling indefinitely are both wrong, and the absence of a
reviewer is not a reason to lower the acceptance path (R5).

**R20.** An acceptance lane is: a named actor or role outside the producing context, given the
acceptance criteria and the evidence, with real authority to return *not accepted*. It may be a
person, another team, a differently-constituted agent, a scheduled governor review, or an external
reviewer.

**R21.** **Convening a lane is the producer's job; staffing it is not.** The producer may open the
lane, supply evidence, and set the deadline. They MUST NOT staff it with themselves, nor with any
actor failing one of the four independence tests in `GOVERNANCE.md` section 3a.

**R22.** A lane MUST carry a deadline and a declared behaviour on no response — escalate, or hold.
**Never auto-accept.** Silence is not acceptance, in any authority profile.

**R23.** Where no lane can be convened at all, that is a finding about capacity: record it, and the
work holds at `submitted`. Work is never accepted for want of a reviewer.

**R24.** This applies with full force when the artifact being accepted is the governance itself.
Authority to proceed and independent acceptance are separate things (`GOVERNANCE.md` R37); being
authorized to act does not make you the right party to judge your own output.

### Staffing the lane — the operator is not a reviewer of first resort

**R25.** A lane MUST be staffed from the reviewers actually available, **including a
differently-constituted automated reviewer**. Routing a routine acceptance to the operator is not an
acceptance path. It converts the accountable human into the default blocker, which is already
forbidden (`GOVERNANCE.md` R8), and it scales worse the more work you delegate.

**R26.** An automated reviewer satisfies acceptance for any control whose authorizer is not required
to be human, provided it meets the independence tests in `GOVERNANCE.md` section 3a. Being automated
neither grants nor removes independence — a fresh session with no write access to the evidence and no
stake in declaring completion is independent; a continuation of the producer's own session is not,
however it is labelled.

**R27.** Protected actions are unaffected. An automated reviewer may accept the *result*; it never
supplies the human authorization a protected action requires. Acceptance and authorization are
separate acts (`GOVERNANCE.md` R37), and only the second is reserved to a human here.

**R28.** The operator is engaged only at a **hard stop**:

| Hard stop | Not a hard stop |
|---|---|
| An action in a protected category needs its human authorization | You would like a second opinion |
| A capability declaration bearing on a protected action is `unknown` | The change feels significant |
| A decision only the operator owns — legal, money, licence, external commitment | You are uncertain and an available reviewer could resolve it |
| No available actor can clear the blocker | Reviewing it yourself feels awkward |

**R29.** Escalating to the operator without a hard stop is **itself a defect**, recorded as one. The
cost is not the interruption; it is that a system which asks for confirmation by default trains its
operator to grant it by default, and the approval stops carrying information.

**R30.** Where an automated reviewer's verdict is used, record what reviewed, on what basis, its
verdict, and its independence position — exactly as for a human reviewer (R11). An unrecorded
automated review is indistinguishable from no review.

**R31.** **Failing to escalate at a genuine hard stop is a defect, and a graver one than escalating
unnecessarily.** R29 alone would penalise one direction of a two-way judgement made by the party that
benefits from getting it wrong — the loophole R6 already names.

The two directions are **not equivalent and MUST NOT be weighted as though they were**. An
unnecessary escalation costs the operator attention and is fully recoverable. A missed hard stop
means an unauthorised protected action, or a legal, money, or licence decision, has **already
happened** — R32 defines that class as protected or irreversible. Where an actor cannot avoid erring,
it errs toward escalating.

**R31.1.** A determination **not** to escalate MUST record the R28 limb relied on. The escalating
branch produces its own artifact — the operator receives a message — while the non-escalating branch
leaves nothing behind unless this is required, which would make R31 unenforceable against the only
branch it exists to constrain.

**R31.2.** Reviewing that record is part of the lane's work. **A lane's acceptance is incomplete
until it has checked the escalation determination**, and a determination later judged wrong is a
finding against the actor who made it. Without a named reviewer, R31 is a rule with no detection
path.

**R32.** The determination is made by the delegated actor, in the moment, against R28 — and it is
**determined by the nature of the action, not by preference or confidence**:

- The action is in a protected category, is irreversible, or is one only the operator owns
  (legal, money, licence, external commitment) → **hard stop**, regardless of how routine it feels.
- Otherwise → **convene a lane**, regardless of how significant it feels.

Confidence is not an input. "This is important" does not resolve to a hard stop.

**"I am unsure" splits, and the split matters more than anything else in this section:**

| Unsure about | Governed by | Resolves to |
|---|---|---|
| The substantive decision — which option is right | R32 | **Convene a lane** |
| Whether the action *is* protected, irreversible, or operator-owned | **R17** — ambiguity takes the more protected classification | **Hard stop** |

An actor that cannot classify its own action has not established that a lane is sufficient. Read
without this split, R32 would hand such an actor textual cover to skip a hard stop it merely failed
to recognise — silently weakening R17 in exactly the case R17 exists for.

The determination is recorded (R31.1) and reviewed (R31.2), and a wrong call in either direction is a
finding against the actor who made it.

**R33.** **The strongest consequence of R25–R32, stated plainly:** work that is critical and
judgement-dependent, but touches no protected category, may be produced, reviewed, and accepted with
no human involved at any point. That is deliberate, and a reader of R4's table would not otherwise
infer it.

What bounds it is enumeration rather than human attention. `GOVERNANCE.md` section 4 enumerates a
baseline set **by action**, not by anyone's assessment of importance, and R15 requires the operator to
extend that set with the concrete actions of their own work.

**Be clear about where this bound is weak.** The baseline is fixed and mechanical; the operator's
extension is neither, and R15's sanction for an incomplete one is definitional rather than corrective
— an unnamed protected action is simply unprotected. So this control **fails silently**: an
under-enumerated operator receives fully autonomous acceptance on precisely the class of harm they
did not think of, and nothing surfaces the gap. That is a different failure signature from
human-in-the-loop review, which degrades noisily because someone sees something that alarms them.

Three things follow, and none of them is optional:

**R33.1.** Enumeration and escalation are not the only controls. **Periodic sampling or post-hoc audit
of autonomous acceptances** is the standard control for a silent-degradation failure shape, and it is
complementary to both. An operator relying on full autonomy for critical work SHOULD run one.

**R33.2.** A near-miss, or a determination later judged wrong under R32, **MUST feed back as a
proposed addition to the operator's enumeration** (R15). Otherwise the remedy in this section is
available only to operators who already anticipated the harm, which is the population that needs it
least.

**R33.3.** An operator who finds this consequence unacceptable for some class of work adds that class
to their enumeration, where it binds every mode.

### Escalating deliberation

**R17.** Where a decision is contested and consequential, deliberation SHOULD be **advisory first**:
positions and reasoning recorded before anyone rules. Ruling first and gathering views afterwards
produces agreement, not analysis.

**R18.** Deliberation MUST have a round cap and a named decider. Unbounded deliberation is a way of
not deciding while appearing busy.

---

## Evidence and acceptance

For any accepted work, you can name: the risk class, the acceptance path required by that class, who
reviewed it, which of the four independence tests held, and what evidence they saw.

## Control points

| Control | Relevance here |
|---|---|
| `work.accept` | The independence requirement per authority profile |
| `integrate.shared-baseline` | Review before work enters what others build on |

Note that `work.accept` requires at least `separate-actor` in **every** authority profile, including
`bounded-autonomous`. Autonomy moves who reviews; it does not remove review.

## Exceptions and stop conditions

**Stop and convene a lane** (R19-R23) when the required acceptance path is unavailable — no
independent reviewer exists, or the only candidate fails a test. Waiting without opening a lane is
just a slower version of stalling, and routing it to the operator instead is not a lane (R25).

**Engage the operator only at a hard stop** (R28). If an available reviewer — human or automated —
could resolve the question, that is where it goes. Proceeding with a review you know is not independent
while recording it as one is a false record.

## Anti-patterns

- An agent reviewing its own output in the same session and confirming it.
- "Reviewed" meaning someone glanced at a summary written by the author.
- Downgrading risk class at the end to fit the review that is actually available.
- Reviews that have never once resulted in rejection.
- Findings recorded with no disposition, closed at end of quarter.
- Five reviewers, one shared summary.
- A decider ruling first, then collecting supporting opinions.

## Templates

`templates/acceptance-record.md` · `templates/decision-record.md`

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
| 1.1.0 | Added R19-R24: convene an acceptance lane when no acceptor is available. |
| 1.2.0 | Added R25-R30: staff lanes from available reviewers including automated ones; operator engaged only at a hard stop. |
| 1.3.0 | Added R31-R33: missed hard stops are defects too; the determination is by action, not confidence; the fully-automated consequence stated plainly. |
| 1.4.0 | Second-lane review: R32 reconciled with R17 (classification uncertainty is a hard stop, not a lane); R31 weighted against the unrecoverable branch, with a recorded artifact and a named reviewer; R33 concedes silent failure, adds sampling and an enumeration-revision trigger. |
