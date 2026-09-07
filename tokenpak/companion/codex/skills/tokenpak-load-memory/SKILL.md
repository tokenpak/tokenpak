---
name: tokenpak-load-memory
description: Retrieve a needed fact from native memory, an identified TokenPak Pak, or the journal when current context and live source are insufficient.
---

# TokenPak Load Memory

Recover only the prior context the current task actually needs.

## Steps

1. Start from the current conversation and live source.
2. Identify the missing fact and the source most likely to contain it.
3. Prefer suitable native memory. Use `load_pak` for an identified Handoff Pak
   or `journal_read` for targeted follow-up. `load_capsule` remains a legacy
   alias of `load_pak`.
4. Verify stale or consequential facts against current source or state.
5. Record only a new durable decision, changed constraint, milestone, blocker,
   or handoff. Recalling an unchanged fact does not require a journal entry.

## When to use

- The user identifies a prior handoff or asks to resume work and current context
  lacks facts needed to continue.
- User references a decision or approach from a past session.
- You need architectural context that isn't in the current files.

## When NOT to use

- The information is in the current codebase (just read the files).
- The user is starting fresh with no prior context needed.
- Listing every Pak or recent session by default. Paks are on demand.
- When the current conversation or live source already supplies the fact.
