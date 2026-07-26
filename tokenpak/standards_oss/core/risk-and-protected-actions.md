---
id: BS-CORE-RISK-AND-PROTECTED-ACTIONS
layer: core
risk_class: critical
default_coverage_profiles: [starter, delegated-work, product-delivery, multi-agent]
control_points:
  [publish.external-irreversible, record.history-alter, data.destroy, data.disclose-external,
   access.credential-change, access.key-custody-transfer, access.grant-escalation,
   recovery.rollback, finance.payment-authorize, finance.payment-destination-new, legal.commitment]
---

# Risk, reversibility, and protected actions

## Purpose

Separate the actions you can take back from the ones you cannot, and put a human in front of the
second kind — permanently, in every operating mode.

## Applies to

All work, all modes. **Read now, then complete the enumeration in your adoption file.** The
categories here are universal; the concrete actions are yours to name.

---

## Requirements

### Reversibility is a property of the world, not of your tooling

**R1.** Classify actions by whether their effect can be undone **from the perspective of everyone
affected**, not by whether your system has an undo:

| Class | Test |
|---|---|
| `reversible` | You can restore the prior state, and nobody outside acted on the change |
| `reversible-with-cost` | Restoration is possible but costs time, money, or trust |
| `irreversible` | Someone outside your control may hold, cache, mirror, or have acted on it |

**R2.** When you cannot enumerate who holds a copy of something, treat its publication as
**irreversible**. "We can delete it" is not the test; "we can delete every copy" is.

**R3.** Deletion is not reversal. Withdrawing something that was public does not unmake the fact that
it was public.

### The three protected categories

**R4.** The protected categories in `GOVERNANCE.md` section 4 bind in every authority profile, including
`bounded-autonomous`. No profile, preset, configuration, or urgency changes this.

**R5.** **Non-delegable** actions are performed by a human. An agent MAY prepare, stage, verify, and
present them for execution; it MUST NOT execute them. Preparing is not performing, and the boundary
between them MUST be structural where possible, not merely instructed.

**R6.** **Human-authorized** actions MAY be executed by an agent, only after authorization that is
specific to *this* action, *this* artifact, *this* environment, and *this* moment. A general
approval to do this kind of thing is not authorization to do this instance of it.

**R7.** **Prohibited** actions are not performed through governed mechanisms at all. If one must
happen, it happens as a break-glass action: outside the governed path, by a human, logged at the
time, reviewed afterwards. A break-glass event that is not reviewed did not happen under control.

**R8.** **No waiver crosses a category** (`GOVERNANCE.md` R12). Urgency does not promote a
human-authorized action to automatic. Incidents do not make non-delegable actions delegable —
incidents are precisely when that boundary earns its cost.

### Naming your own

**R9.** Operators MUST enumerate the concrete actions in their own work belonging to each category,
in the adoption file. Category names are not an enumeration.

**R10.** The enumeration MUST be reviewed when the work changes shape — new integration, new
external party, new payment path, new data source.

**R11.** An action that is not enumerated but plainly falls into a category is still protected. The
obligation attaches to the act, not to whether you remembered to write it down. A gap in the list is
a defect to fix, never a permission.

### Before an irreversible action

**R12.** Before executing anything irreversible, all of the following MUST hold:

1. The exact artifact or target is identified by something stable — an identity, hash, version, or
   enumerated list. Not a description, not a pattern, not "the latest".
2. The authorization names that identity.
3. The required evidence exists and has been reviewed.
4. What happens if this is wrong is known and stated.
5. The state was re-verified **immediately before** the action, not only when it was authorized.

**R13.** Any material change to the artifact between authorization and execution **invalidates the
authorization**. Re-authorize.

**R14.** Enumerate before destroying. Destructive operations MUST act on an explicit list, never on a
pattern match evaluated at execution time. Patterns match things you did not anticipate.

**R15.** Prefer **superseding** to withdrawing. Publishing a correction usually serves people better
than removing the thing they already depend on, and it preserves the record.

### Uncertainty

**R16.** Where a capability declaration relevant to a protected action is `unknown`, the action MUST
NOT proceed. Resolve the declaration.

**R17.** Where classification is ambiguous, treat the action as belonging to the **more protected**
category until someone with authority classifies it. Defaulting to permissive under uncertainty
inverts the purpose of the classification.

---

## Evidence and acceptance

For every protected action executed, retain: the control ID, the artifact identity, the authorizing
actor, the timestamp, the evidence reviewed, and the outcome. This is not paperwork — it is what lets
you answer "who did this, and on what basis" three months later, which is exactly when you will need
to.

## Control points

Every control in `controls/controls.yaml` marked `protected: true`. See the generated table in
`controls/GENERATED-mode-tables.md` for the resolved values under each authority profile — and note
that the protected rows read identically across all of them. That is the design.

## Exceptions and stop conditions

**Stop** when: an action is protected and you lack fresh specific authorization; a relevant
declaration is `unknown`; the artifact changed after authorization; you cannot identify the target
stably; or you cannot state what happens if it is wrong.

The only exception path is break-glass (R7), and it is not an exception to the requirements — it is a
different, logged, human-performed route with mandatory review.

## Anti-patterns

- A standing approval to "handle releases" treated as approval for each release.
- Approving against a description, then executing against whatever is current.
- An agent holding credentials it only needs to *prepare* an action a human must perform.
- Destroying by pattern: `delete where name matches …`.
- "We'll classify it properly later" — later is after the irreversible action.
- Treating "the tool has an undo" as reversibility when third parties already acted.
- An emergency used to move an action across a protected category.

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
