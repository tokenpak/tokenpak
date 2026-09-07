---
name: tokenpak-budget-aware-implementation
description: Execute a coding task with budget awareness when cost is material, the user asks about it, or a TokenPak guard reports a constraint. Routine companion accounting calls are unnecessary.
---

# TokenPak Budget-Aware Implementation

Work on a coding task while respecting TokenPak's automatic accounting and
spend guard. The native harness remains responsible for the coding workflow.

## When to inspect budget

- Call `check_budget` when the user asks about spend, a planned provider action
  depends on remaining budget, or the automatic guard reports a constraint.
- Call `estimate_tokens` only for a genuine go/no-go choice about including
  unusually large content.
- If a guard blocks the request, surface the block and preserve its reason. Do
  not shorten protected instructions or work around the guard.

## During work

- Keep work on the critical path and use the harness's native context handling.
- `prune_context` is available for verbose disposable output when retaining it
  would materially obstruct the task. Preserve exact errors and evidence that
  affect a decision.

## If budget gets tight

- Prioritize the smallest complete, safe result.
- Tell the user which work is blocked or deferred and why.

## Durable records

Use `journal_write` only when a durable decision, changed constraint, verified
milestone, material blocker, or handoff needs to survive the session. Routine
reads, edits, tests, and completion messages do not require a journal entry.
