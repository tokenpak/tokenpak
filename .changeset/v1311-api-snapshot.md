---
---

Release-gate: public-API snapshot update for v1.31.1.

Generated from `main` with #370, #371, #372 and #373 merged, because this
release lands after them.

Added, 42 symbols: the Windows launcher modules
`tokenpak.companion.windows_claude` (7), `tokenpak.companion.codex.windows_launcher`
(9) and `tokenpak.companion.hooks.windows_native` (5);
`tokenpak.proxy.circuit_breaker.is_startup_check`; the Spend Guard modules
`tokenpak.proxy.spend_guard.media` (5), `tokenpak.proxy.spend_guard.approvals` (4)
and `tokenpak.proxy.spend_guard.usage_anchor` (4) and one name in
`tokenpak.proxy.spend_guard.estimator`; and six module-level names in
`tokenpak.status.worker`.

Removed, 1 symbol: `tokenpak.status.worker.valid_session`. It was an incidental
import, not an interface; the function is unchanged at
`tokenpak.status.binding.valid_session`, and nothing imports it from
`tokenpak.status.worker`.
