---
name: tokenpak-retrospective
description: End-of-session retrospective that summarizes what was accomplished, records decisions and rationale, captures unfinished work, and estimates cost. Use at the end of a session or when wrapping up a task to ensure continuity for future sessions.
---

# TokenPak Retrospective

Wrap up the current session with a structured closeout.

## Steps

1. Call `check_budget` to get final session spend.
2. Call `journal_read` for this session to review what was journaled.
3. Write a closeout summary via `journal_write` covering:
   - What was accomplished (bullet points).
   - Key decisions made and why.
   - What's unfinished and what the next step would be.
   - Any gotchas or surprises found.
4. Report to the user:
   - Session cost
   - Accomplishments (concise)
   - Next steps if work remains

## Output format

```
Session summary:
- Done: [1-3 bullet points]
- Decisions: [any architectural or design choices]
- Remaining: [what's left, if anything]
- Cost: $X.XX (N requests)
```

Keep it under 15 lines. The journal has the details — this is the headline.
