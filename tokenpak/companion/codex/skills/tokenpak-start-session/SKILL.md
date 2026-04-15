---
name: tokenpak-start-session
description: Initialize a TokenPak companion session. Sets up budget tracking, checks for relevant capsules from prior sessions, seeds the journal, and reports companion status. Use at the beginning of a new coding session or when resuming work.
---

# TokenPak Start Session

Initialize the companion for this session.

## Steps

1. Call `session_info` to verify the companion is active and get config.
2. Call `check_budget` to see daily spend and remaining budget.
3. Call `journal_read` (no session_id) to list recent sessions — show the user the last 3.
4. If the user mentioned resuming prior work or a specific task, call `load_capsule` to find relevant context.
5. Call `journal_write` with a brief note: what the user wants to accomplish this session.

## Output

Report to the user:
- Companion status (profile, budget)
- Today's spend so far
- Recent sessions (1-line each)
- Whether a relevant capsule was loaded

Keep it under 10 lines. Do not dump raw JSON.
