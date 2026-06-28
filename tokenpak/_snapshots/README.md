# Release-gate snapshots

These JSON files are the canonical, checked-in artifacts that gate every PR per **Standard 30 — Release-Gate Trust Contract** (ratified 2026-05-09).

| File | Contract § | Purpose |
|---|---|---|
| `public-api.json` | §7 (R7) | Sorted list of every public symbol on every `tokenpak.*` module. PR fails if a public symbol is removed without a `removes-public-symbol:` declaration in the PR body. |
| `telemetry-schema.json` | §7 (R7) | Frozen DDL of every user-facing SQLite store (`~/.tokenpak/telemetry.db`, `~/.tokenpak/spend_guard.db`). PR fails on schema drift unless the same PR carries a migration test and multi-hop migration passes. |
| `workflow-steps.json` | §13.3 (R11) | Sorted CI step tuples for `release*.yml` + `release-rehearsal.yml`. PR fails if a step is removed without a `removes-ci-step: <step.id>` declaration. |

## Updating

```bash
# Regenerate one
make api-snapshot
make workflow-steps-snapshot
make telemetry-snapshot

# Regenerate all
make release-gate-snapshots

# Validate (CI uses these)
make release-gate-check
```

## When a snapshot diff appears

CI fails the PR with a teaching message that names the rule. Authoring path:

1. Confirm the change is intentional.
2. Add a `.changeset/<date>-<slug>.md` entry describing why.
3. For symbol removals: add `removes-public-symbol: <fully.qualified.symbol>` to the PR body.
4. For workflow-step removals: add `removes-ci-step: <step.id>` to the PR body.
5. For schema bumps: ship a migration test in the same PR.
6. Regenerate the snapshot (`make ...-snapshot`), commit, push.

## Why these are checked in (not generated at CI time)

The drift signal IS the checked-in file. Drifting source means the PR has changed the public surface; CI compares the freshly-generated snapshot against the committed one and fails on diff. If snapshots were CI-generated, there would be nothing to drift against.
