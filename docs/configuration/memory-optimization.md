# Process-local memory optimization

**Maturity:** Available Now

TokenPak can calculate and apply a deterministic MemoryGuard configuration for
the current machine. Optimization is process-local: it does not edit systemd,
crontab, shell profiles, kernel settings, or other operating-system policy.

## Plan before applying

```bash
tokenpak config optimize --plan
tokenpak config optimize --plan --profile conservative --mode observe
tokenpak config optimize --plan --json
```

The plan records physical memory, the effective cgroup memory limit when one is
present, policy versions, thresholds, and measurement provenance. Identical
normalized inputs produce identical canonical JSON and the same plan SHA-256.
Planning is read-only.

Profiles are `conservative`, `balanced`, and `throughput`. Modes are:

- `off`: keep MemoryGuard disabled;
- `observe`: sample and report pressure without garbage collection, allocator
  trimming, or cache eviction;
- `auto`: take the bounded process-local actions exposed by MemoryGuard.

## Apply and verify

```bash
tokenpak config optimize --apply
tokenpak config optimize --status
```

Apply re-probes the machine under an exclusive lock, recalculates the plan, and
writes only TokenPak-owned state under the resolved TokenPak home. To bind an
apply to a previously reviewed plan:

```bash
tokenpak config optimize --apply --expect-hash <plan-sha256>
```

Restart the TokenPak proxy after applying so it loads the managed plan. Once
loaded, MemoryGuard runs in its existing background thread; no separate tuning
daemon is installed.

Any `TOKENPAK_MEMORY_*` environment variable selects the explicit environment
configuration source and causes the managed file to be ignored entirely. The
sources are never merged. Unknown variables in that reserved namespace, empty
values, and invalid explicit values fail loudly. A corrupt managed file instead
fails safely to MemoryGuard off and appears as a warning in proxy health.

## Roll back

```bash
tokenpak config optimize --rollback
```

Apply records the exact prior bytes or records that the file was absent.
Rollback restores that preimage and removes the one-level receipt. If the
managed file changed after apply, rollback refuses instead of overwriting the
drift. Review the status output first; `--rollback --force` is the explicit
escape hatch for restoring the recorded preimage.

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | Success |
| 2 | Host or derived memory budget unsupported |
| 3 | Apply refused or invalid apply arguments |
| 4 | Rollback refused, including external drift |
| 5 | Managed config or rollback receipt is corrupt |
