---
---

Release-gate: ratchet the public-API snapshot for the new `tokenpak upgrade`
CLI command.

This records the intentional additive public surface introduced by the Pro
upgrade command: the command module, its parser/handler helpers, its URL
constants, and the shared upgrade URL constants consumed by `license` and
`status` output. No public symbols are removed.
