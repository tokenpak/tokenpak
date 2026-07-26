# Validation checklist — <what is being validated>

> Template. Replace everything in angle brackets. Delete this line.
> The **row mechanics** are the point of this template, not the rows themselves. Every row must have
> an owner, a result, and a pointer to evidence — an unowned row gets skipped, and a row without
> evidence is an assertion.

| Field | Value |
|---|---|
| **Validating** | `<artifact, release, restore, or claim set>` |
| **Identity** | `<version, hash, or commit>` |
| **Date** | `<YYYY-MM-DD>` |
| **Overall** | `<pass \| pass-with-findings \| fail>` |

## Rows

| # | Check | Blocking? | Owner | Result | Evidence |
|---|---|---|---|---|---|
| 1 | `<what is checked>` | `<yes/no>` | `<who>` | `<pass \| fail \| not-run \| unavailable>` | `<pointer>` |
| 2 | | | | | |

**Result vocabulary matters.** `not-run` and `unavailable` are distinct from `fail`, and none of them
is `pass` (`BS-CORE-TRUTH-AND-EVIDENCE` R2). Do not collapse them.

## Findings

| # | Finding | Severity | Disposition | Owner |
|---|---|---|---|---|

## Not checked

<What was in scope and not checked, and why. An empty list here is a claim that everything in scope
was checked — make sure it is true.>

---

## Example row sets

> **Non-normative.** These are starting points, not requirements. Adapt them; a wrong prefilled row
> is worse than a blank one.

### Restore rehearsal

| Check |
|---|
| Backup located and readable |
| Restored into a real target, not a dry run |
| Restored data complete against a known reference |
| Restored system actually usable |
| Elapsed time measured — this is your real recovery time |
| Procedure followed by someone who did not write it |

### Release artifact

| Check |
|---|
| Artifact identity matches what was verified |
| Installs cleanly on a machine with no prior state |
| Contents are what was intended, and nothing else |
| Version reported matches the version published |
| Documented quickstart executes as written |
| No credentials or development-only material included |

### Published claims sweep

| Check |
|---|
| Each claim classed |
| Each claim's basis located and dated |
| Measurements still valid for the current version |
| Limitations present and findable |
| Support and response commitments match present capacity |
