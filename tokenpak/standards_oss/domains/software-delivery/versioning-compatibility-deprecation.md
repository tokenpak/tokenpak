---
id: BS-SWDEL-VERSIONING-COMPATIBILITY-DEPRECATION
layer: domains/software-delivery
risk_class: high
default_coverage_profiles: [product-delivery, multi-agent]
control_points: [publish.external-irreversible, commit.external-promise]
---

# Versioning, compatibility, and deprecation

## Purpose

Make the version number a promise your users can rely on, and change things without breaking people
silently.

## Applies to

Anything other people depend on by version. **Read now if you publish releases.**

---

## Requirements

### The version contract

**R1.** The version communicates the nature of the change:

| Increment | Means |
|---|---|
| **Major** | Something that worked will now behave differently or stop working |
| **Minor** | Capability added; existing usage unaffected |
| **Patch** | Defect corrected; no intended behaviour change |

**R2.** **Breaking means: an unchanged invocation produces a different result.** Not "we removed an
API" — that is one instance. Changed defaults, changed output format, changed error behaviour,
stricter validation, and changed ordering are all breaking if a caller who changed nothing sees
something different.

**R3.** A changed default is a breaking change. It is the most commonly missed one, because the code
still runs.

**R4.** Version numbers are not marketing. Withholding a major bump because it sounds alarming
transfers the alarm to your users, later, without warning.

**R5.** **One version fact per releasable unit**, in one place, with everything else deriving from
it. Multiple version declarations disagree eventually, and the disagreement surfaces at release.

**R6.** Where several units release together, their compatibility relationship is declared. "They are
released together" is not a compatibility statement.

### Maturity and expectations

**R7.** Declare a maturity level and hold to what it implies:

| Level | Implies |
|---|---|
| Experimental | May change or disappear at any time; say so at the point of use |
| Beta | Breaking changes possible between minors, announced |
| Stable | Breaking changes only on major, with a migration path |

**R8.** Nothing is stable by default because it has been around a while. Maturity is declared.

**R9.** An interface without a declared level is treated as **experimental** by you and will be
treated as **stable** by your users. Declare it.

### Deprecation

**R10.** A deprecation states: what is deprecated, what replaces it, from which version, and **until
which version it will keep working**.

**R11.** **A deprecation with no removal target is not a deprecation.** It is a warning that will be
ignored, then a removal that surprises people.

**R12.** The deprecation window scales with maturity: stable gets a long window and at least one
release carrying both old and new; experimental may go immediately.

**R13.** Deprecation warnings reach the person who can act — at the point of use, not only in release
notes nobody reads.

**R14.** **No breaking change without a migration path.** "Rewrite the calling code" is not a
migration path; a documented mapping from old usage to new is.

**R15.** Removal happens at the announced version, and the removal is announced again when it lands.

### Support states

**R16.** Say plainly what state each version is in: supported, security-fixes-only, or end-of-life.

**R17.** Where you do not support something, refuse clearly rather than degrading quietly. A typed
"this is not supported in this version" beats behaviour that half-works.

**R18.** Support states are commitments (`BS-COMMITMENTS-STAKEHOLDER-AND-SUPPORT`). Do not publish
one you cannot meet.

---

## Evidence and acceptance

For any release you can state: what changed, why that increment, what is deprecated with removal
targets, and what state each supported version is in — and a user can find all of it without asking.

## Control points

| Control | Relevance here |
|---|---|
| `publish.external-irreversible` | The version, once published, is what people pin to |
| `commit.external-promise` | Support states and deprecation windows are commitments |

## Exceptions and stop conditions

**Stop** when a change is breaking and the increment does not reflect it, or when a removal is
proposed with no migration path. Both are decisions requiring authority, not release-day judgement
calls.

## Anti-patterns

- A patch release that changes a default.
- Two files declaring the version, disagreeing.
- "Beta" on something a thousand people depend on.
- A deprecation warning present for three years with no removal.
- Removal in the release that announced the deprecation.
- Warnings only in release notes.
- An unsupported case that half-works instead of refusing.

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
