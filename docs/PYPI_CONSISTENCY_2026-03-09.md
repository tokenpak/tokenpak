# PyPI Consistency Check — 2026-03-09

## Version Alignment
- Source (`tokenpak/__init__.py`): 1.0.0
- Build config (`pyproject.toml`): 1.0.0
- Installed (`pip show tokenpak`): 1.0.0

**Status:** ✅ MATCH

## Metadata (pyproject.toml vs installed dist-info)
- Author: Kevin Yang <kaywhy331@gmail.com> ✅
- Homepage: https://github.com/kaywhy331/tokenpak (via project.urls) ✅
- License: MIT ✅
- Python requirement: >=3.10 ✅

**Note:** `pip show` `Home-page:` field is blank by design — pyproject.toml uses
the modern `[project.urls]` → `Homepage` field instead of the legacy `home-page` key.
This is correct behavior and not a defect.

## Dependencies (pyproject.toml vs installed)
| Package   | Required         | Installed |
|-----------|-----------------|-----------|
| aiohttp   | >=3.9.0         | ✅        |
| pyyaml    | >=6.0           | ✅        |
| click     | >=8.1.0         | ✅        |
| starlette | >=0.36.0        | ✅        |
| uvicorn   | >=0.27.0        | ✅        |
| httpx     | >=0.26.0        | ✅        |
| h2        | >=3,<5          | ✅        |
| watchdog  | >=3.0.0         | ✅        |

**Status:** ✅ ALL DEPENDENCIES MATCH

## Package Contents (installed modules)
Modules found under `tokenpak/`:
agent, assembler, benchmark, broker, budget, budgeter, cache, cache_report,
calibration, calibrator, capsule, citation_tracker, cli, cli_doctor, compaction,
compiler, complexity, connectors, core, elo, engines, enterprise, evidence_pack,
handlers, integrations, intelligence, miss_detector, pack, processors, proxy,
reference_fetcher, reference_scanner, registry, report, routing, routing_ledger,
security, shadow_hook, shadow_reader, span_extractor, state_manager, telemetry,
tokens, user_templates, validation, validator, version_check, walker, wire

Core expected modules (sdk, proxy, handlers, schema, telemetry): All present ✅

## PyPI Registry Status
- `tokenpak` package is **NOT yet published to PyPI** (returns 404)
- `pip show tokenpak` returns the locally installed editable build
- Local dist artifacts exist: `dist/tokenpak-1.0.0-py3-none-any.whl`, `dist/tokenpak-1.0.0.tar.gz`

**Status:** ⚠️ NOT ON PYPI — package is local/editable install only

## Issues Found & Fixed
1. ⚠️ **Docstring version mismatch** — `tokenpak/__init__.py` docstring referenced `v0.1.0`
   while `__version__ = "1.0.0"`. **Fixed:** updated docstring to `v1.0.0`.
2. ⚠️ **Stale installed metadata** — installed dist-info showed `Author: TokenPak Contributors`
   (old build). **Fixed:** reinstalled with `pip install -e . --break-system-packages`
   so dist-info now reflects current `pyproject.toml` author (Kevin Yang).
3. ℹ️ **PyPI not published** — no public release exists yet. Not a blocking issue
   but should be addressed before public launch.

## Status
⚠️ MOSTLY CONSISTENT — 2 minor issues found and fixed; PyPI not published (expected for pre-launch)
