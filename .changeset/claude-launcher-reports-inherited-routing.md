---
"tokenpak": patch
---

Report inherited routing in the Claude companion banner.

`tokenpak claude` hands the child a copy of its own environment, so an
`ANTHROPIC_BASE_URL` already exported in the shell routes Claude even when
TokenPak detects no proxy and selects nothing of its own. The banner only ever
announced TokenPak's own selection, so in that case it printed nothing about
routing at all — which reads as a direct connection to the provider.

That is the wrong impression in both directions. If the inherited URL is
TokenPak's proxy, the operator is told nothing about a session that is in fact
being measured; if it is some other endpoint, the operator may believe TokenPak
is accounting for traffic that never reaches it.

The banner now names the inherited endpoint when TokenPak selected no proxy of
its own:

```text
Routed by inherited ANTHROPIC_BASE_URL → http://127.0.0.1:8766 (not selected by TokenPak)
```

Reporting only. Routing, precedence, and the environment handed to the child
are unchanged: an explicit selection still overrides the inherited value, and an
inherited value is still passed through untouched when TokenPak selects nothing.
