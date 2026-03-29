# TokenPak Python Compatibility Matrix

TokenPak requires Python **3.10+** (`requires-python = ">=3.10"`).

## Supported Versions

| Python | Status | Notes |
|--------|--------|-------|
| 3.10   | ✅ Supported | Minimum supported version |
| 3.11   | ✅ Supported | Primary CI version (coverage gates run here) |
| 3.12   | ✅ Supported | Tested in CI matrix |
| 3.13   | ✅ Supported | Added to CI matrix 2026-03-26 |
| 3.9    | ❌ Not supported | Requires `match`/`case` and 3.10+ typing features |

## Notes

- **Python 3.13** is GC-cycle-aware (PEP 703 no-GIL experimental builds exist but are not tested).
  Standard CPython 3.13 is fully supported.
- CI runs `fail-fast: false` — all versions tested regardless of individual failures.
- Coverage gates (Tier-1 ≥50%, overall ≥45%) run on Python 3.11 only.
- See `.github/workflows/ci.yml` for the full matrix definition.

## Updating This File

When a new Python version is released or support is dropped, update:
1. This table
2. `.github/workflows/ci.yml` matrix
3. `packages/core/pyproject.toml` `requires-python`
4. README badge
