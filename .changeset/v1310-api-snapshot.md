---
---

Release-gate: public-API snapshot update for v1.31.0.

Additive change only. New public symbols: `tokenpak.cli.commands.home_migrate`
(the plan-first split-home merge behind `tokenpak home migrate`),
`tokenpak.cli.commands.update_apply` (the idle-gated restart behind
`tokenpak update apply`), `tokenpak.core.runtime.update_pending`
(`PendingUpdate`, `detect`, `cached_detect`) and `tokenpak.cli.exit_codes.EXIT_BUSY`.
No symbol is removed or changed.
