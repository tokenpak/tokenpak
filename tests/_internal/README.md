# `tests/_internal/` — Internal-only test surface

## Purpose

This directory holds tests that **cannot run on a slim OSS install** because they depend on the closed-source `tokenpak._internal/*` namespace (governed by Std 25 §1.1) at runtime — for module-level imports, `mock.patch("tokenpak._internal.config.…")` calls, fixtures that read `_internal.config` helpers, or any other unreplaceable runtime tie to the closed-source surface.

This is **not** a "tests of internal features" directory. The tests here largely exercise public OSS features (anonymous metrics, proxy budget enforcement, stats footer rendering); they live here because their *implementation* depends on `_internal.config` for things like opt-in gating or runtime config reads, and that dependency cannot be mocked away without rewriting the test from scratch.

## How exclusion works

`pyproject.toml` adds `tests/_internal` to `[tool.pytest.ini_options].norecursedirs`. Pytest never collects from this tree on the default OSS gate. No CLI `--ignore` flag is used; the exclusion is config-driven and visible in source.

Result on a slim `[dev]` install:

```bash
pytest tests/                                   # collects only OSS tests
pytest tests/ --ignore-glob='**/_internal/**'   # same set; no-op
```

## How to run these tests on a Pro / dev-with-extras install

When `tokenpak._internal` is present (Pro daemon-equipped install or developer dev-with-extras), invoke pytest with `tests/_internal/` explicitly:

```bash
pytest tests/_internal/ -q
```

A future Pro-side CI matrix can run the union by overriding `norecursedirs` in a separate config file or by passing `--override-ini='norecursedirs='` (scoped, not a broad bypass).

## What lives here today

| File | Purpose | Why it's internal-only |
|---|---|---|
| `test_stats_footer.py` | OSS stats-footer rendering tests | Reads `tokenpak._internal.config.get_stats_footer_enabled()` |
| `test_metrics_reporter.py` | OSS anonymous-metrics reporter tests | `mock.patch("tokenpak._internal.config.get_metrics_enabled")` |
| `proxy/test_budget_enforcement.py` | OSS proxy budget-enforcement tests | Reads `tokenpak._internal.config.BUDGET_*` constants |
| `cli/test_metrics_mode_fields.py` | OSS metrics mode-detection tests (record-stores-mode subset; the schema and pure-detect carve-outs live at `tests/cli/test_anon_metrics_schema.py` and `tests/cli/test_consumption_mode_detect.py` per TSR-03 + TSR-04) | `mock.patch("tokenpak._internal.config.get_metrics_enabled")` plus `from tokenpak._internal.config import get_active_profile` |

## What does NOT live here

Tests that have only a **partial** runtime dependency on `_internal` (where most of the file is OSS-public and only a few specific tests need closed-source) stay in their original location with **per-test** `pytest.importorskip("tokenpak._internal")` guards — see `tests/test_quick_suite.py` for the canonical example. Moving such files wholesale would lose OSS-public coverage on the unaffected tests.

## Authority

- **Std 25 §1.1** — declares `tokenpak._internal/*` closed-source per the Pro-tier boundary
- **Std 32 §1.3** — slim-OSS surface excludes `tokenpak._internal/*`
- **Initiative `2026-05-08-release-test-suite-recovery` Phase 4** (TSR-07) — relocated this directory + added the `pyproject.toml` `norecursedirs` exclusion

## Lessons recorded

The `88d3d9deb0` `_internal/` cleanup refactor stripped public-OSS adjacent code on two distinct occasions:

1. **TSR-03** — `MetricsRecord` v1.1 schema fields (`active_profile`, `consumption_mode`, `SCHEMA_VERSION="1.1"`, SQLite migration)
2. **TSR-04** — `tokenpak.telemetry.anon_metrics.detect_consumption_mode()` function

Both restorations are now complete. Std 25 §1.4 amendment proposal queued at `~/vault/02_COMMAND_CENTER/proposals/2026-05-08-std-25-boundary-refactor-safety-amendment.md` to formalize the refactor-safety rule that would have prevented these regressions.

**Future namespace-cleanup refactors must verify they do not strip ANY public-OSS surface — dataclasses, helper functions, migrations, or constants — adjacent to the closed-source code being deleted.**
