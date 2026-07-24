---
---

Release-gate: bind public-API snapshot generation to the checkout that owns
the generator script and fail closed when TokenPak resolves from another
editable installation. This prevents a green snapshot check from validating
stale source in a different worktree.
