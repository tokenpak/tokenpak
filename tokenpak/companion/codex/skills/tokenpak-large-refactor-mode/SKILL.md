---
name: tokenpak-large-refactor-mode
description: Disciplined mode for large refactors that span many files. Plans coherent phases, validates changed behavior, and records only semantic milestones needed for continuity.
---

# TokenPak Large Refactor Mode

Structured approach for multi-file refactors that may span sessions.

## Phase 1: Plan

1. Map the change: which files, what order, and what dependencies.
2. Break work into coherent phases sized for the change. File count alone does
   not define a phase or create a journaling obligation.
3. Inspect budget only when the user asks, a concrete decision depends on it,
   or a guard reports a constraint.

## Phase 2: Execute per phase

For each phase:
1. Read only the files needed for this phase. Use `estimate_tokens` only for a
   genuine go/no-go choice about including unusually large content.
2. Make the changes.
3. Run tests / type-check for this phase.
4. Record a semantic milestone only when it changes durable project state or
   creates a handoff, decision, changed constraint, or material blocker.
5. Use native context handling; prune only verbose disposable output while
   retaining exact errors and load-bearing evidence.

## Phase 3: Verify

1. Run focused checks appropriate to the changed behavior, then broader checks
   only when the repository policy or a failure's blast radius requires them.
2. Report the exact checks and unresolved limitations.

## If session runs out

- Write one concise handoff record with the outcome, reason, source, and next
  step when another session will need it.
- Load an identified Handoff Pak when resuming across sessions and current
  context does not already contain the needed facts.

## Key discipline

- Read only the files needed for the current phase.
- Test after each phase, not just at the end.
