---
id: BS-SWDEL-DOCUMENTATION-AND-PRESENTATION
layer: domains/software-delivery
risk_class: moderate
default_coverage_profiles: [product-delivery, multi-agent]
control_points: [publish.external-revocable, commit.external-promise]
---

# Documentation and project presentation

## Purpose

Make the project usable by someone who did not build it, and honest about what it is.

## Applies to

Anything with users or contributors outside the immediate team. **Read when triggered** by your first
external user.

---

## Requirements

### Documentation that matches reality

**R1.** Documentation describes what the software **does**, not what it was designed to do. A
documented feature that does not work is a defect in both.

**R2.** Every example is **executed as written** before publication, on the version it documents.
Examples are the most-copied and least-checked part of any documentation.

**R3.** Documentation is updated in the same change as the behaviour it describes. Deferred
documentation updates are not done later; they are done never.

**R4.** Where behaviour is version-dependent, say which version. Undated documentation is assumed
current and is wrong the moment it is not.

**R5.** **Limitations are documented alongside capabilities.** What does not work, what is not
supported, and what is known to be broken belong where someone deciding whether to use it will see
them.

**R6.** Documenting something that does not exist yet is a false claim, whatever the intent. Mark
planned behaviour explicitly, or leave it out.

### Layers

**R7.** Provide, at minimum: what this is and whether it fits (in thirty seconds), how to get
started, how to do the common things, and a complete reference.

**R8.** Do not make the reader read everything to do the first thing. Progressive disclosure is a
requirement, not a style preference.

**R9.** Error messages are documentation. When someone hits an error, that text is what they read
first, and often all they read.

### Presentation

**R10.** The entry page answers, above the fold: what this is, who it is for, what it costs, and what
state it is in.

**R11.** Maturity is stated where people see it, not buried. Shipping something experimental is fine;
letting people discover its maturity through failure is not.

**R12.** Include the files that make the project usable and trustworthy: licence, how to contribute,
how to report a security issue, how to get support, and what changed between versions.

**R13.** The changelog says what changed **from the user's perspective** — what breaks, what is new,
what to do about it. Not a list of commit messages.

### Contribution

**R14.** State what you accept, how to submit it, what standard it must meet, and what happens after
submission — including the possibility of "no, and here is why".

**R15.** State response expectations honestly. "Maintained on a best-effort basis, responses may take
weeks" respects people's time. An unstated expectation becomes an assumed one, then a complaint.

**R16.** Contribution expectations are commitments (`BS-COMMITMENTS-STAKEHOLDER-AND-SUPPORT`). Do not
publish a review turnaround you cannot meet.

### Security reporting

**R17.** Publish how to report a security issue, and where. Without a stated path, reports arrive
publicly.

**R18.** State what a reporter can expect: acknowledgement time, and whether you will credit them.

**R19.** Honour it. A published security contact that nobody monitors is worse than none — it
converts a private report into a false sense that someone is handling it.

### Licensing

**R20.** Publish a licence. Without one, others have no stated permission to use, modify, or
redistribute the work — which is a decision you have made by omission rather than on purpose.

**R21.** **An absent licence is surfaced, never inferred** (`GOVERNANCE.md` R47–R49). Tooling,
agents, and collaborators MUST NOT select a licence, copy one from a surrounding project, or treat
a licence classifier in packaging metadata as though it were the licence itself. They report that
none was found and leave the choice to whoever owns the work.

**R22.** Licence choice is an **operator decision with legal consequence**. It is not delegable to an
agent, and no recommendation should be offered in place of the decision. Where the operator has
recorded a standing order covering licensing, that governs (`GOVERNANCE.md` R51).

**R23.** Where licence metadata appears in more than one place — a licence file, packaging metadata,
documentation, file headers — those MUST agree. A packaging classifier that contradicts the licence
file is a defect, and it is the version most tools will believe.

**R24.** Third-party licence obligations are checked before adoption, not before release
(`dependencies-and-supply-chain.md` R4), and attribution requirements are honoured where they apply.

---

## Evidence and acceptance

Someone unfamiliar with the project can, from the published material alone: decide whether it fits,
get it running, do the common task, find the limitations, and know how to report a problem.

Test this with an actual newcomer. You cannot evaluate your own documentation's onboarding path — you
already know the answers.

## Control points

| Control | Relevance here |
|---|---|
| `publish.external-revocable` | Documentation is a published claim surface |
| `commit.external-promise` | Support and contribution expectations are commitments |

## Exceptions and stop conditions

**Stop** when documentation would describe behaviour that does not exist, or when publishing a
support or response commitment you cannot meet.

## Anti-patterns

- A quickstart that fails on a clean machine.
- Examples last executed two major versions ago.
- Limitations in a wiki page nobody links to.
- A changelog of raw commit messages.
- "Contributions welcome" with unanswered submissions from a year ago.
- A security address routed to an unmonitored mailbox.
- Docs updated "in a follow-up" that never lands.

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
