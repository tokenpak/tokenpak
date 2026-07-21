---
---

CLI: add deterministic, reversible process-local memory optimization.

`tokenpak config optimize` can now plan, apply, inspect, and roll back a
MemoryGuard configuration derived from physical and cgroup memory limits. The
managed state uses canonical hashes, atomic writes, drift detection, and an
exact preimage receipt. Runtime environment overrides remain exclusive, and
the optimizer never changes operating-system services, schedules, or limits.
