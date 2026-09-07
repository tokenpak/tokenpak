---
name: tokenpak-retrospective
description: Create a concise end-of-session handoff when the user asks for one or future work needs durable continuity. Avoid duplicate stop narration and routine accounting calls.
---

# TokenPak Retrospective

Wrap up the current session with a structured closeout.

## Steps

1. Use the current conversation and verified task results as the source.
2. Write one handoff via `journal_write` only when future continuity needs it,
   covering:
   - What was accomplished (bullet points).
   - Key decisions made and why.
   - What's unfinished and what the next step would be.
   - Any gotchas or surprises found.
3. Report to the user:
   - Accomplishments (concise)
   - Next steps if work remains

Call `check_budget` only if the user asks for cost or a budget decision depends
on it. Do not repeat a handoff already written during the work; the stop hook's
accounting closeout remains separate.

## Output format

```
Session summary:
- Done: [1-3 bullet points]
- Decisions: [any architectural or design choices]
- Remaining: [what's left, if anything]
- Evidence: [tests or receipts that matter]
```

Keep the report proportional to the work and preserve material failures.
