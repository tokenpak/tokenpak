# `_tip_schemas/` — vendored TIP-1.0 schemas

**Do not edit files in this tree directly.** They are a vendored mirror of `tokenpak/registry:schemas/` shipped inside the tokenpak wheel so `tokenpak doctor --conformance` works without a separate registry checkout.

## Layout

```
tokenpak/_tip_schemas/
└── schemas/
    ├── tip/
    │   ├── capabilities.schema.json
    │   ├── companion-journal-row.schema.json
    │   ├── compatibility.schema.json
    │   ├── error.schema.json
    │   ├── headers.schema.json
    │   ├── metadata.schema.json
    │   └── telemetry-event.schema.json
    └── manifests/
        ├── adapter.schema.json
        ├── client-profile.schema.json
        ├── plugin.schema.json
        └── provider-profile.schema.json
```

The `schemas/` intermediate directory matches the validator's expected layout: `TOKENPAK_REGISTRY_ROOT/schemas/tip/<name>.schema.json`.

## Sync checklist

Any TIP-MINOR schema change touches three surfaces in order. All three must land in the same release cycle, or `tokenpak doctor --conformance` will drift against the authoritative spec.

1. **Upstream source** — `tokenpak/registry:schemas/`. The schema change lands there first, with a new conformance vector + updated `capability-catalog.json` if capability labels change.

2. **Validator PyPI release** — republish `tokenpak-tip-validator` so `_SCHEMA_PATHS` in `tokenpak_tip_validator.schema` includes the new schema name. Without this, callers using the installed validator hit `KeyError: unknown TIP schema name`.

3. **Vendored copy (this tree)** — copy the updated `schemas/tip/*.json` and `schemas/manifests/*.json` from `tokenpak/registry:schemas/` into `tokenpak/_tip_schemas/schemas/`. Bump `tokenpak.__version__`.

## Sync command (operator runs)

```bash
# From the tokenpak repo root, with tokenpak/registry checked out at ../registry:
rm -f tokenpak/_tip_schemas/schemas/tip/*.json \
      tokenpak/_tip_schemas/schemas/manifests/*.json
cp ../registry/schemas/tip/*.json        tokenpak/_tip_schemas/schemas/tip/
cp ../registry/schemas/manifests/*.json  tokenpak/_tip_schemas/schemas/manifests/
git diff -- tokenpak/_tip_schemas/       # review before committing
pytest tests/conformance/ -m conformance # verify no drift
tokenpak doctor --conformance            # operator-facing smoke
```

## Detection

`tokenpak doctor --conformance` degrades gracefully when the vendored copy is missing or partial:

- If any schema path is unreachable → exit 2 with a clear pointer to install/sync.
- If the installed validator's `_SCHEMA_PATHS` lacks a known schema name (PyPI lagging the registry) → WARN, not FAIL, with upgrade hint.

The SC-06 pytest suite is stricter — runs against `TOKENPAK_REGISTRY_ROOT` (CI-provided registry checkout), so any drift between this vendored tree and the upstream source will surface as a schema-validation failure on CI.

## Provenance

Vendored 2026-04-22 as part of Phase TIP-SC (SC-07). Canonical upstream: https://github.com/tokenpak/registry/tree/main/schemas.
