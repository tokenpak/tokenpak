---
---

Release-gate: public-API snapshot update for the proxy lifecycle fixes.

Additive change only. New public symbols: `tokenpak.proxy.economics_gate`
(`EconomicsGate`, `EconomicsBusy`, `get_economics_gate`, `client_disconnected`,
`DEFAULT_MAX_CONCURRENT`, `DEFAULT_WAIT_S`), which bounds and shares
session-economics evaluations, and `tokenpak.proxy.cache_invalidator.DEFAULT_CACHE_MAX_BYTES`,
the default size cap of the cache-invalidator session cache: 7 symbols in
all. No symbol is removed or changed.
