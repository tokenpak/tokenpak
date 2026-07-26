---
id: BS-COMMITMENTS-EXTERNAL-CLAIMS-AND-EVIDENCE
layer: commitments
risk_class: high
default_coverage_profiles: [product-delivery, multi-agent]
control_points: [publish.external-revocable, publish.external-irreversible, commit.external-promise]
---

# External claims and evidence

## Purpose

Keep what you say publicly — about your work, your product, your results, or your capability —
tethered to what you can show.

## Applies to

Anything said outside your own control: marketing, documentation, proposals, reports to clients,
posts, pitch decks, release notes. **Read when triggered** by your first external statement.

---

## Requirements

### Claim classes

**R1.** Classify a claim before making it. Each class has a different evidence bar:

| Class | Example | Requires |
|---|---|---|
| **Descriptive** | "This does X" | X exists and works as described |
| **Comparative** | "Faster than Y" | A reproducible method, stated conditions, and Y's actual measurement |
| **Quantitative** | "Reduces cost 40%" | The measurement, its conditions, its date, its variance |
| **Assurance** | "Secure", "private", "reliable" | A named control, and evidence it is active |
| **Capacity** | "24-hour support" | The staffing or automation that makes it true today |
| **Forward-looking** | "Will support Z" | Marked clearly as intent, with no implied date unless committed |

**R2.** A claim MUST NOT be made without evidence of the type its class requires. This applies to a
sentence in a proposal as much as to a headline.

**R3.** Quantitative claims MUST state conditions and SHOULD be ranges. A single number implies a
precision that measured results with variance do not have.

**R4.** Assurance claims are the highest-risk class because they are hardest to check and most
damaging when wrong. "Private" without naming what is not collected is not a claim; it is a mood.

**R5.** Capacity claims MUST match **present** capacity, not intended capacity. A support promise
your calendar cannot honour is a commitment you will break.

### Limitations

**R6.** Where you state capabilities, state limitations with **comparable prominence**. A limitations
section that nobody finds is not disclosure.

**R7.** Known defects, gaps, and unsupported cases MUST be discoverable before someone depends on
them, not after.

**R8.** "Not yet supported" is a complete and respectable statement. Silence about a gap reads as
support and is discovered at the worst moment.

### Substantiation

**R9.** Every non-trivial public claim MUST have a traceable basis: what was measured or verified,
when, and by whom. Keep the mapping from claim to basis somewhere you can find it.

**R10.** When the underlying thing changes, the claim MUST be re-checked. Claims decay silently —
what was true at version 1 quietly becomes false at version 3.

**R11.** Claims MUST NOT be inherited from a prior version without re-checking. Copying last
release's numbers forward is fabrication with extra steps.

**R12.** Where a result depends on configuration, the configuration MUST be stated. Results from a
tuned setup presented as defaults are misleading even when accurate.

### Corrections

**R13.** A published claim discovered to be wrong MUST be corrected where it was published, promptly,
saying what was wrong.

**R14.** Correct **forward**. Silently editing the original so the error never appears is
`record.history-alter` — protected, and rarely the right call for a public statement.

**R15.** If someone may have decided something based on the wrong claim, tell them directly. A
corrected page does not reach the person who already acted.

### Comparisons

**R16.** Comparative claims about others MUST use a method you would accept if it were applied to
you, on a version you can name, with conditions stated.

**R17.** Where you cannot measure a competitor fairly, describe your own fit instead of asserting
superiority. A neutral, accurate description of what you suit outperforms an unsupportable
comparison, and it survives contact with a skeptical reader.

---

## Evidence and acceptance

For every public claim, you can produce its class, its basis, its date, and who checked it last. Run
this as a periodic sweep — claims rot without anyone editing them.

## Control points

| Control | Relevance here |
|---|---|
| `publish.external-revocable` | Substantiation before publication |
| `publish.external-irreversible` | Same, plus the substantiation is retained as part of the record |
| `commit.external-promise` | Capacity claims are commitments; see the stakeholder standard |

## Exceptions and stop conditions

**Stop** when: a claim's basis cannot be located; the measurement is stale and the underlying thing
changed; a claim depends on capacity you do not currently have; or you are asked to state something
you cannot check.

## Anti-patterns

- "Fast", "secure", "reliable" as adjectives with nothing behind them.
- A benchmark from six versions ago, still on the page.
- Best-of-five presented as typical.
- Limitations in a collapsed section at the bottom.
- Support hours nobody staffs.
- Quietly editing a wrong number and moving on.
- Comparing against a competitor version you never actually ran.

## Templates

`templates/validation-checklist.md` (claims sweep) · `templates/decision-record.md`

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
