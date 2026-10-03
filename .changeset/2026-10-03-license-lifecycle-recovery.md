---
"tokenpak": patch
---

Keep an installed, current license when a different key is activated, and report a
lapsed license as expired. `tokenpak activate` previously wrote a pending stub over
`license.json` before anything verified the new key, discarding a working license
and its signature. It now refuses, leaves the file unchanged, and names
`tokenpak deactivate` as the way to replace it; the same key is accepted as
already active, and an expired or pending license can still be replaced.

`tokenpak license` and `tokenpak features` now honour `expires_at` and the issuer's
`grace_days`, so a lapsed license reads as expired and no longer grants Pro through
its tier, instead of reporting Pro as active. An unreadable `expires_at` counts as
expired and an unrepresentable `grace_days` is ignored, so corrupt values fail
closed rather than raising. A feature named in an explicit `features_override`
keeps its existing precedence on the `features` display. No public symbol is added
or removed.
