# Published migration baselines

These are the exact `tokenpak/_snapshots/telemetry-schema.json` bytes from the
canonical public annotated releases v1.23.0 through v1.28.0. The manifest records
each tag object, peeled commit, tree, snapshot Git blob, SHA256 and byte count.
Identical payloads share a file; the gate still creates a fresh database for
every release and every registered store.

The three snapshots from v1.23.0 through v1.25.0 share one payload. Those releases
predate the guard reservation/domain tables present in the other three snapshots.
All six snapshots contain the telemetry, spend-guard and monitor stores. Nothing
is reconstructed from current schema, and no historical Python is executed.

The gate permits only the admitted table/index DDL and seeds every historical
table. Valid harmless header JSON and distinct alert dates avoid triggering the
intentional credential-redaction and duplicate-alert cleanup migrations. A
singleton guard-domain table receives one row; other tables receive two, with
typed values and nullable fields. These preservation seeds complement focused
tests of deliberate cleanup and new accounting-field defaults.

Run from the repository root in a clean, isolated test environment:

```sh
python scripts/release_gate/migration_multihop.py
python -m pytest -q tests/release_gate/test_migration_multihop.py
```

The manifest is an offline provenance pin, not a query for the latest public
release. Before a later release, reconcile its publication anchor and replace
the set with the six latest published minor baselines in the current major.
Verify annotated refs and snapshot blobs against the canonical public repository
when updating the manifest. Missing history or a store is a failure, never a
successful skip. A major with fewer than six published minor baselines requires
an explicit reviewed boundary policy; this six-baseline gate cannot be weakened
by a command-line subset.

The resulting evidence covers forward migration and repeated initialization.
It does not certify downgrade execution. Keep the pre-upgrade database backup
and matching older runtime for rollback.
