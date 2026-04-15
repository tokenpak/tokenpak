---
name: tokenpak-large-refactor-mode
description: Disciplined mode for large refactors that span many files. Plans the work upfront, journals each phase, uses capsules for continuity across sessions, and avoids context bloat by reading only what's needed per phase. Use for renames, architecture changes, migration work, or any change touching 5+ files.
---

# TokenPak Large Refactor Mode

Structured approach for multi-file refactors that may span sessions.

## Phase 1: Plan

1. Call `check_budget` — large refactors are expensive. Warn if < 50% budget.
2. Map the change: which files, what order, what dependencies.
3. Call `journal_write` with the plan (files, order, rationale).
4. Break into phases of 3-5 files each.

## Phase 2: Execute per phase

For each phase:
1. Read only the files for this phase (use `estimate_tokens` if unsure about size).
2. Make the changes.
3. Run tests / type-check for this phase.
4. Call `journal_write`: "Phase N done: [files changed], [tests status]."
5. If context is getting large, call `prune_context` on verbose test output.

## Phase 3: Verify

1. Run the full test suite.
2. Call `journal_write` with final status.
3. Call `check_budget` and report spend.

## If session runs out

- Call `journal_write` with: completed phases, remaining phases, any gotchas found.
- The next session can `load_capsule` to resume exactly where you left off.

## Key discipline

- Never read all files at once. Read per-phase.
- Journal each phase completion — this is your save point.
- Test after each phase, not just at the end.
