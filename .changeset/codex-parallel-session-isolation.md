---
"tokenpak": patch
---

Fix Codex parallel-session isolation with shared, deterministic per-workspace,
and unique per-session homes. Provision only allowlisted non-runtime files,
install integrations into the selected home, publish validated lifecycle
sentinels atomically, retain global skills when other homes can reference them, and
enforce bounded preserve-first isolated-home retention with doctor visibility.
Lock diagnostics now identify live and stopped holders on Linux, fail closed on
inspection races, and retain a bounded portable contention fallback elsewhere.
