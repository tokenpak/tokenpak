---
id: BS-SWDEL-CHANGE-AND-CODE-PRACTICE
layer: domains/software-delivery
risk_class: moderate
default_coverage_profiles: [product-delivery, multi-agent]
control_points: [integrate.shared-baseline, work.scope-change]
---

# Change and code practice

## Purpose

Keep changes small enough to review, structured enough to understand, and honest about what they do.

## Applies to

Work that modifies source or configuration in a shipped system. **Read now if you write code.**

---

## Requirements

### Shape of a change

**R1.** A change does **one thing**. A change doing several things cannot be reviewed, reverted, or
explained as a unit.

**R2.** Changes are **scope-tight**: only files the work required. Unrelated cleanup sweeps in with
the change, gets attributed to it, and hides from the review that should have covered it.

**R3.** Unrelated refactoring travels separately. A behaviour change buried in a formatting change is
invisible in review — which is the practical definition of unreviewed.

**R4.** The change message states **what changed and why**. What is visible in the diff; why is not,
and why is what the next reader needs.

**R5.** Where a change is large by necessity, say so and say why, and structure it so review is
possible — ordered commits, a reading order, a summary of the shape.

### Structure

**R6.** Boundaries between components are declared and respected. A dependency crossing a boundary
that was not designed for it is a decision, recorded — not a convenience.

**R7.** Dependency direction is one-way. Cycles make every part of the cycle untestable alone.

**R8.** Duplication is a **signal**, not automatically a defect. Two similar things that change for
different reasons should stay separate; unifying them couples two independent futures.

**R9.** Configuration is resolved through one documented precedence chain. Multiple silent sources
produce behaviour nobody can predict from reading any of them.

**R10.** Deviations from the structure are documented **where they are**, with the reason. An
undocumented exception is indistinguishable from a mistake and will be "fixed" by someone later.

### Errors

**R11.** Errors say what was being attempted, what failed, and what the caller can do. An error that
only reports failure moves diagnosis onto the reader.

**R12.** Failures are not swallowed. Catching an error and continuing without recording it converts a
detectable fault into an undetectable one.

**R13.** Do not substitute a default for a failed operation on any path where the difference matters.
Returning empty from a failed fetch is the same defect as reporting zero for an unavailable
measurement.

**R14.** Behaviour under failure is stated: retry (how often, with what backoff), fail closed, or
degrade — and degradation is visible to the caller, never silent.

### Interfaces

**R15.** Public interfaces — anything another program or person depends on — change under the version
contract in `versioning-compatibility-deprecation.md`.

**R16.** Machine-readable output is stable and parseable. Human-readable output may change; the two
are separate contracts and MUST NOT be the same surface.

**R17.** Anything destructive defaults to dry-run; apply is explicit
(`BS-CONTINUITY-WORKSPACE-AND-FILE-SAFETY` R9).

---

## Evidence and acceptance

A reviewer can state what the change does, why, and what it does not touch — from the change and its
message alone.

## Control points

| Control | Relevance here |
|---|---|
| `integrate.shared-baseline` | Review before entering the shared line |
| `work.scope-change` | Discovering the change needs to be bigger than authorized |

## Exceptions and stop conditions

**Stop** when a change cannot be made without crossing a declared boundary, or without growing beyond
its authorized envelope. Both are decisions, not judgement calls for the executor.

## Anti-patterns

- One change containing a fix, a refactor, and a formatting pass.
- "Fixed bug" as the entire message.
- A cycle between components, described as pragmatic.
- Three config sources with undocumented precedence.
- `except: pass`.
- Returning an empty list when the fetch failed.
- Human-facing output parsed by a script.

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
