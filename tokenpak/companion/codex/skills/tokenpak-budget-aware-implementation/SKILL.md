---
name: tokenpak-budget-aware-implementation
description: Execute a coding task with active budget awareness. Checks remaining budget before starting, estimates cost of planned operations, prioritizes high-value work if budget is tight, and warns before expensive steps. Use for any non-trivial implementation task.
---

# TokenPak Budget-Aware Implementation

Work on a coding task while actively managing token budget.

## Before starting

1. Call `check_budget` to see remaining budget.
2. Call `estimate_tokens` on any large files you plan to read.
3. If budget remaining < 30% of daily cap, tell the user and ask which parts are highest priority.

## During work

- Before reading files > 500 lines, call `estimate_tokens` first.
- After large tool outputs (test results, build logs), call `prune_context` to compress before reasoning.
- Every 3-4 tool calls, mentally check: am I still on the critical path or drifting?

## If budget gets tight

- Summarize what you've done so far via `journal_write`.
- Tell the user what remains and estimated cost.
- Suggest: finish the critical path now, defer nice-to-haves.

## After completing

- Call `journal_write` with: what was done, what decisions were made, what's left.
- Call `check_budget` and report final spend.
