---
id: BS-GOVERNANCE
standards_version: 1.4.0
risk_class: critical
default_coverage_profiles: [starter, delegated-work, product-delivery, multi-agent]
---

# Governance

The authority model for this corpus: what words mean, who may authorize what, which protections
cannot be waived, how exceptions work, what conformance claims are honest, and how the corpus
changes.

This document owns those definitions. Other standards describe behavior and reference the definitions
here. Where another standard appears to redefine a term or an authority rule, this document governs
and the other standard has a defect.

---

## 1. Normative language

| Word | Meaning |
|---|---|
| **MUST** / **MUST NOT** | Absolute. Violation is a defect, not a tradeoff. |
| **SHOULD** / **SHOULD NOT** | Strong default. Deviation requires a recorded reason. |
| **MAY** | Genuinely optional. |

Requirements are numbered `R1`, `R2`, … within each standard and referenced as
`<standard-id> R<n>`. Numbers are stable: a withdrawn requirement is marked withdrawn and its
number is never reused.

---

## 2. The governing separation

> **Mode may change who executes an action and who authorizes it. It must not change what is
> correct, what evidence is required, what is recorded, or which protections cannot be waived.**

**R1.** Correctness requirements MUST read identically in all authority profiles.

**R2.** Evidence requirements MUST NOT be reduced by any authority profile. Higher autonomy
increases the value of records, and MUST NOT reduce them.

**R3.** Where a standard's behavior varies by authority, it MUST express that variance by referencing
a control point in `controls/controls.yaml`. It MUST NOT restate authority values in prose.

**R4.** An authority profile MUST NOT be interpreted as permission to skip a standard. Coverage
determines whether a standard applies; authority determines who signs off inside it.

---

## 3. Actors and authority

Roles are functions, not job titles or software. One person can hold several; an agent can hold
some but never all.

| Role | Function | May authorize protected actions? |
|---|---|---|
| **Operator** | Owns the work and its consequences. Accountable externally. | Yes |
| **Delegate** | Holds authority the operator explicitly granted, within a stated envelope and expiry. | Only within the granted envelope |
| **Reviewer** | Evaluates work independently of whoever produced it. | Only where a control names a reviewer as authorizer |
| **Executor** | Performs work. Human or agent. | No |
| **Auditor** | Inspects records after the fact. Never authorizes, never executes. | No |

**R5.** An executor MUST NOT authorize its own work, set a terminal state on its own work, or accept
its own deliverable. This holds in every authority profile, including `bounded-autonomous`.

**R6.** Authority MUST NOT exceed the actual permission envelope. If an actor is *assigned*
authority it technically cannot exercise, that is a configuration defect to fix, not a fiction to
work around.

**R7.** Delegated authority MUST carry: the granting authority, the scope, the resource envelope, and
an expiry. A grant without an expiry is a defect. An asynchronous approval MUST NOT become
open-ended standing authority.

**R8.** The escalation target MUST NOT be the default blocker. Routine review and routine
uncertainty are resolved by an available reviewer, automated if that is what is available; the
accountable human is engaged only at a hard stop as defined in
`delegation/independent-review-and-acceptance.md` R28. Every escalation path MUST define, in
advance, what happens when the target does not respond within its stated window: proceed within a
named envelope, or stop cleanly. "Wait indefinitely" and "assume approval" are both defects.

### 3a. Independence

A reviewer or acceptor is **independent** of the work only if all four hold:

1. **Authorship** — it did not produce the work or materially co-author it.
2. **Context** — it does not share the producer's working session, and does not take the producer's
   assumptions as its sole input.
3. **Evidence control** — it does not have write access to the evidence it is evaluating.
4. **Decision separation** — it does not share the producer's incentive to declare completion.

**R9.** Where a control requires independent review, all four tests MUST hold. Failing any one means
the review is not independent, whatever it is called.

**R10.** Verification diversity SHOULD scale with risk. For decisions that are critical *and* depend
on model judgement rather than a checkable fact, review by a differently-constituted reviewer (a
different person, a different tool, a different model) SHOULD be used. This is a recommendation, not
a universal requirement — see `delegation/independent-review-and-acceptance.md`.

---

## 4. Protected actions

Some actions are protected regardless of profile. Three categories, and the distinction is
operational, not rhetorical.

### 4a. Non-delegable — a human performs the action

The action itself is performed by a human. An agent may prepare, stage, and verify, but MUST NOT
execute.

- Agreeing to legal terms or signing on behalf of the operator.
- Transferring or exporting a root credential, signing key, or account ownership.
- Establishing a **new** destination for money to move to.
- Changing who holds authority over the work.

### 4b. Human-authorized — an agent may execute, after fresh authorization for this instance

Authorization is specific to this action, this artifact, this environment, and this moment.

- Publishing anything externally that cannot be recalled.
- Altering or deleting an existing record, including history.
- Destroying data.
- Disclosing data outside its declared boundary.
- Reverting to a prior state where the reversion itself has external effect.
- Widening any actor's permissions.
- Authorizing a payment to an already-established destination.

### 4c. Prohibited — not performed through these mechanisms at all

Empty by default. Operators declare their own. A prohibited action is not "hard to do" — it is
outside what the governed path performs. If it must happen, it happens as a **break-glass** action:
outside the governed mechanisms, by a human, logged at the time, reviewed afterwards.

### 4d. Rules that hold across all three

**R11.** Every protected control MUST carry `mode_override_allowed: false`. The validator MUST reject
any profile that attempts to weaken one.

**R12.** **No waiver transforms a category.** A waiver cannot make a prohibited action allowed, a
non-delegable action delegable, or a human-authorized action automatic. Waivers operate *inside* a
category, never across.

**R13.** Protected-action definitions MUST be loaded and readable regardless of coverage profile.
Ignorance by configuration is not available. Operating procedures for a protected action activate
when the corresponding capability exists.

**R14.** If a capability declaration relevant to a protected action is `unknown`, the action MUST NOT
proceed. Resolve the declaration first.

**R15.** Operators MUST enumerate the concrete actions in their own work that fall into 4a–4c.
Category names alone are not an enumeration. This list lives in the adoption file and is part of
first-run setup.

---

## 5. Control points

`controls/controls.yaml` is the canonical registry. Each entry is an **action class** — named for
what the action does to the world, not for the tool that performs it.

**R16.** Authority values MUST be set on control classes. Domain modules MAY register concrete
**instances** that map onto a class; an instance inherits its class's authority and MAY tighten it,
never loosen it.

**R17.** Human-readable mode tables MUST be generated from `controls/controls.yaml` and the authority
profiles. They MUST NOT be hand-maintained. Generated files carry a generated-file marker, the source
hash, and the generator version; regenerating them in a clean tree MUST produce no diff.

Canonical per-control fields:

| Field | Required in v1 | Meaning |
|---|---|---|
| `executor` | yes | Who or what performs it |
| `authorizer` | yes | Who authorizes it |
| `authorization_type` | yes | `explicit` · `standing-envelope` · `none-required` |
| `independence_requirement` | yes | `none` · `separate-actor` · `independent` (section 3a) |
| `mode_override_allowed` | yes | Whether an authority profile may change these values |
| `authorization_timing` | no | `before` · `before-and-revalidated` · `after-within-sla` |
| `scope_or_envelope` | no | Limits on what the authorization covers |
| `expiration` | no | When the authorization stops being valid |
| `required_evidence` | no | What must exist before proceeding |
| `reversibility_class` | no | `reversible` · `reversible-with-cost` · `irreversible` |
| `fallback` | no | What happens if the authorizer is unavailable |
| `enforcement` | no | `{level: procedural \| static \| runtime, mechanism: …}` |

**R18.** Any control with `reversibility_class: irreversible` or belonging to a protected category
MUST populate **all** canonical fields. Optional-by-default applies only to low-risk controls.

**R19.** The compiled effective profile MUST contain every field explicitly resolved. Nothing is left
implicitly defaulted at read time — compilation resolves and emits the value.

---

## 6. Authority profiles

Three, plus a tightening example. Each sets a default value per control class.

| Profile | Default posture |
|---|---|
| `assisted` | Operator authorizes all substantive actions |
| `supervised` | Named control points route to operator or reviewer; the rest proceeds with a record |
| `bounded-autonomous` | Work proceeds on evidence with batched notification; section 4 protections unchanged |

**R20.** No authority profile may alter a `mode_override_allowed: false` control. `bounded-autonomous`
is bounded by section 4, permanently and by construction.

**R21.** Authority resolves **per control point**, not globally. A profile is a set of defaults; an
operator may tighten any individual control without forking a profile or a document.

**R22.** Local project configuration MAY tighten any value — narrower envelope, shorter expiry, more
evidence, stricter independence. It MUST NOT loosen a protected field, remove a floor, or reduce
required evidence. Tightening is silent and allowed; loosening is a validation error.

---

## 7. Conformance — what a claim here means

Four levels. Say which one you mean, every time.

| Level | Meaning |
|---|---|
| `declared` | You have stated an intent or configuration. Nothing has checked it. |
| `validated` | Your configuration is internally consistent and schema-valid. Behavior is unchecked. |
| `enforced` | Something structurally prevents violation. |
| `verified` | Actual behavior was observed to match, with evidence retained. |

**R23.** **This corpus at version 1.x provides `declared` and `validated` only.** A valid
`bounded-autonomous` profile is a coherent declaration of intent. It is not a runtime bound on
anything. Any statement implying otherwise is false.

**R24.** A control MUST NOT be described as `enforced` unless the enforcing mechanism exists and is
named in its `enforcement` field. A gate that claims to be required while nothing implements it is
worse than an acknowledged gap — it converts a known risk into a false assurance.

**R25.** Conformance claims about this corpus MUST NOT use the language of compliance,
certification, guarantee, or audit standard. It is operating guidance with machine-checkable
declarations.

---

## 8. Ownership and conflict

| Surface | Owns |
|---|---|
| `GOVERNANCE.md` | Terms, invariants, authority semantics, protected categories, precedence |
| Standards | Behavioral requirements in their subject area |
| `controls/controls.yaml` | Control-point values |
| Profiles | Coverage and authority defaults |
| Project adoption file | Declared capabilities, local tightening, enumerated protected actions |
| Generated tables, examples | Nothing. They are output. |

**R26.** No value may be maintained authoritatively in both prose and `controls/controls.yaml`.

**R27.** A contradiction between prose and data is a **defect that fails validation**. It MUST NOT be
resolved by a precedence rule at read time. Fix the source.

**R28.** Examples, sample rows, and template placeholders are never active policy. Unresolved
placeholders (`REPLACE_ME`) MUST fail validation rather than silently becoming policy.

---

## 9. Exceptions and waivers

**R29.** A waiver MUST record: the requirement waived, the reason, the authorizing actor, an expiry
date, the compensating control, and the review date. Any missing field means no waiver exists.

**R30.** Waivers do not inherit. A waiver granted for one release, environment, or artifact does not
carry to the next. Each instance is a fresh decision.

**R31.** A waiver MUST NOT cross a protected category (R12).

**R32.** Waiver accumulation is a signal, not a cost of doing business. Repeated waivers of the same
requirement MUST trigger a decision: fix the requirement, fix the practice, or record why the
requirement does not fit the work. Perpetual waiving is a defect in the standard or the process.

**R33.** Expedited paths (urgent fixes, incident response) are **expedited, not exempt**. They may
reorder or compress steps and defer non-blocking evidence. They MUST record what was deferred and
MUST repay it within a stated window.

---

## 10. Authorization lifecycle

**R34.** An authorization binds to: the control ID, the specific action, the environment, the
identity of the artifact, the scope, the resource envelope, the expiry, whether it is single-use or
reusable, the authorizing actor, and the evidence reviewed.

**R35.** A material change to the artifact **invalidates** the authorization. Re-authorize.

**R36.** Authorization in one environment is not authorization in another. Approval to publish
somewhere provisional is not approval to publish somewhere permanent.

**R37.** **Authorization to execute is not acceptance of the result, and acceptance cannot
retroactively authorize.** They are separate acts by separate rules. Evidence produced after the fact
never converts an unauthorized action into an authorized one.

Lifecycle states — semantic; storing them is optional:

```
proposed → evidence-ready → authorized → executed → verified → accepted → closed
                                     ↘ stopped → rolled-back → reviewed
```

---

## 11. Corpus lifecycle

**R38.** Changes to this corpus follow: **propose → decide → record → supersede.** A change without a
recorded decision has not happened.

**R39.** **Supersede; do not delete.** A superseded requirement remains readable, marked superseded,
pointing to what replaced it. Deleting the record destroys the reasoning.

**R40.** Change classes and proportional sign-off:

| Class | Example | Authorizer |
|---|---|---|
| Editorial | Wording, typo, clarification with no behavior change | Any maintainer |
| Substantive | Changes a requirement's meaning or adds one | Operator or designated reviewer |
| Structural | Changes the authority model, a protected category, or the conformance ladder | Operator only |

**R41.** A change touching section 2, section 4, or section 7 is structural by definition.

**R42.** The corpus SHOULD be reviewed on a stated cadence. A standard that has not been read since
it was written is not known to be true. Record the review date; an unreviewed standard is a
liability, not an asset.

---

## 12. Versioning

**R43.** The corpus's version is **authoritative in exactly one place**: this file's frontmatter.
Other files MAY carry `standards_version` only as a **compatibility declaration**, never as an
independent statement of the version. Every such declaration MUST equal the authoritative value, and
the validator MUST check each one — an unchecked copy is a second source of truth wearing a different
name (R26, R27).

**R44.** The version is semantic: **major** for a change that invalidates existing conformant
configurations, **minor** for added requirements or controls, **patch** for editorial change.

**R45.** Profile files carry `standards_version`. The validator MUST check compatibility and MUST
report a mismatch rather than guessing.

**R46.** Deprecation: no requirement is removed without a stated replacement or an explicit statement
that it no longer applies, and no removal without a migration note. A deprecation with no removal
target is not a deprecation.

---

## 13. Absent answers and standing orders

Two rules about what this corpus and its tooling do when they do not know something.

### 13a. Never fabricate a direction on the operator's behalf

**R47.** Where a governing answer is **absent** — no licence declared, no retention period set, no
escalation contact named, no owner assigned — the corpus and its tooling MUST **surface the gap as a
decision for the operator**. They MUST NOT synthesize a plausible answer, infer one from
surroundings, or apply a convention as though it had been chosen.

**R48.** A surfaced gap MUST state what is missing, why it matters, and who decides. It MUST NOT
recommend a specific answer where the choice carries legal, financial, or ownership consequence —
naming an option in those cases is how a default becomes a decision nobody made.

**R49.** An absent answer is reported as **absent**, never as a default that happens to be in force.
This is the governance form of the value distinction in `core/truth-and-evidence.md` R2: a licence
nobody chose is `not-measured`, not "the usual one".

**R50.** Tooling MUST NOT block on an absent answer that carries no protected action; it reports and
continues. Where the absent answer *does* gate a protected action, R14 applies and the action stops.

> **Worked example — licensing.** A project with no licence file has not chosen "no licence", and has
> not chosen a permissive one either. The correct behaviour is to report that no licence was found,
> note that this determines whether and how others may use the work, and leave the choice to the
> operator. Selecting one, or copying whatever the surrounding project uses, is fabrication.

### 13b. Standing orders take precedence

**R51.** An operator MAY record **standing orders** — durable instructions about how their work is to
be run — in their adoption file. Standing orders **take precedence over this corpus's defaults and
recommendations**, including over the behaviour in section 13a: where a standing order already answers a
question, the tooling follows it and does not re-surface the gap.

**R52.** A standing order MUST record its author and the date it took effect, so precedence can be
traced.

**R53.** Standing orders operate on defaults and recommendations. They do **not** weaken a protected
field, a protected category, or a required evidence floor — that path is `local_tightening`, which
tightens only (R22), and no waiver crosses a category (R12). An operator who intends to change a
protected boundary is making a structural change under R40, not issuing a standing order.

**R54.** Where a standing order and a surfaced gap conflict, the standing order governs and the
tooling records that it did. Silent precedence is how an operator loses track of what is deciding
their behaviour.

## 14. Glossary

| Term | Definition |
|---|---|
| **Action class** | A control point named for its effect on the world, not the tool used |
| **Break-glass** | A controlled path outside the governed mechanisms, human-performed, logged, reviewed |
| **Capability declaration** | The operator's stated answer about what their work does; the normative source |
| **Coverage profile** | Which standards apply |
| **Authority profile** | Who authorizes what |
| **Effective profile** | Compiled result of coverage + authority + local tightening, all fields resolved |
| **Envelope** | The bounded scope within which delegated authority is valid |
| **Independent** | Satisfying all four tests in section 3a |
| **Instance** | A concrete action registered against an action class by a domain module |
| **Protected action** | An action in a section 4 category; not overridable by profile |
| **Receipt** | Retained evidence that a specific check ran and what it found |
| **Supersede** | Replace a rule while preserving the record of the old one |

Extend this glossary in your own copy under a clearly marked local section. Do not redefine a term
above — add a new one.
