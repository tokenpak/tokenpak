---
name: tokenpak-start-session
description: Inspect TokenPak companion status or recover an identified handoff when the user requests it or current context is missing required prior-session facts.
---

# TokenPak Start Session

Use companion startup and retrieval tools only when they answer a real task
question. Accounting and spend enforcement already run out of band.

## Steps

1. Start from the user's request and current conversation.
2. Call `session_info` only for setup, diagnosis, or an explicit status request.
3. Call `check_budget` only for an explicit budget question or decision.
4. If a needed prior fact is missing, query the most likely source. Use native
   memory when suitable, `load_pak` for an identified handoff, or
   `journal_read` for targeted follow-up.
5. Do not list the Pak catalog or recent sessions automatically.
6. Use `journal_write` only for a durable decision, changed constraint,
   verified milestone, material blocker, or handoff.

## Output

Report only the information requested or needed for the task, including any
missing or stale evidence.
