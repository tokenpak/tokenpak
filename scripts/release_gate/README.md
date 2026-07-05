# Release-gate helper scripts

These scripts implement the binding release-gate contract. The public map below lists each helper and the Make target that enforces it.

## Scripts

| Script | Make target | Std 30 § | Purpose |
|---|---|---|---|
| `gen_api_snapshot.py` | `make api-snapshot` / `make api-snapshot-check` | §7 (R7) | Generate / validate `tokenpak/_snapshots/public-api.json`. |
| `api_snapshot_diff.py` | `make api-snapshot-diff BASE=… HEAD=…` | §6 (R6) + Std 21 §11 | Diff public-api snapshots between two git refs. |
| `taxonomy_check.py` | `make taxonomy-check` | §5 (R5) + Std 02 §13 | Validate every collected test has exactly one taxonomy marker (`oss` / `optional` / `internal` / `legacy`). |
| `gen_workflow_steps.py` | `make workflow-steps-snapshot` / `--check` | §13.3 (R11) + Std 21 §12 | Generate / validate `tokenpak/_snapshots/workflow-steps.json`. |
| `gen_telemetry_schema.py` | `make telemetry-snapshot` / `--check` | §7 (R7) | Generate / validate `tokenpak/_snapshots/telemetry-schema.json`. |
| `migration_multihop.py` | `make migration-multihop` | §14.1 (R16) + Std 10 §E9 | Run migrations from each of the last 6 minor-version baselines to HEAD. Stub-active until first migration lands. |

## CI integration

These scripts are invoked by the `release-rehearsal.yml` workflow (weekly dry run) and by the install-shape matrix step in `release.yml`. Standard 30 §11 is the canonical reference-implementation map.

## Snapshots

Lives under `tokenpak/_snapshots/`. Three frozen JSON artifacts:

- `public-api.json` — sorted public symbol list (Std 30 §7).
- `telemetry-schema.json` — frozen DDL for user-facing SQLite stores (Std 30 §7).
- `workflow-steps.json` — sorted CI step tuples for the workflow-step ratchet (Std 30 §13.3).

PR rejection messages cite these scripts + their `--check` invocations, so contributors learn the rule from the failure.
