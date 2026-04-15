---
name: tokenpak-load-memory
description: Load context from a prior session using TokenPak capsules and journal entries. Use when resuming work, referencing past decisions, or needing context that would otherwise require re-reading many files. Avoids wasteful re-exploration.
---

# TokenPak Load Memory

Recall context from prior sessions without re-reading the codebase.

## Steps

1. Call `load_capsule` with no session_id to list available capsules.
2. Show the user the available capsules (session ID, date, size).
3. If the user specifies which session, call `load_capsule` with that session_id.
4. If no specific session, check `journal_read` for recent sessions and pick the most relevant based on the user's current task.
5. After loading, call `journal_write` to note what was recalled and why.

## When to use

- User says "pick up where we left off" or "continue from last time."
- User references a decision or approach from a past session.
- You need architectural context that isn't in the current files.

## When NOT to use

- The information is in the current codebase (just read the files).
- The user is starting fresh with no prior context needed.
- Every session start — capsules are on-demand, not automatic.
