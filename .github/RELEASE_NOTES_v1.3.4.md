# tokenpak v1.3.4

## TIP-1.0 self-conformance — release-gate hotfix

v1.3.4 recovers the TIP-SC phase from a failed v1.3.3 release attempt. Content is the same as v1.3.3 plus a narrowly-scoped release-gate fix so the tag can actually ship to PyPI.

### What failed in v1.3.3

The v1.3.3 tag landed on `main` but `release.yml`'s `test` step ran the full `pytest tests/` suite, which now includes `tests/conformance/`. Those tests resolve TIP schemas via `tokenpak-tip-validator`; the release workflow did not check out `tokenpak/registry` or set `TOKENPAK_REGISTRY_ROOT`, so the tests failed with `schema.unavailable`. Downstream build / GitHub Release / PyPI publish jobs did not run.

`v1.3.3` is retained on the repo as a burned tag for auditability. No PyPI release was published for 1.3.3.

### Fix in v1.3.4

- **`release.yml` test step** excludes `tests/conformance/` via `--ignore=tests/conformance`. The conformance suite is the canonical job of `tip-self-conformance.yml` per DECISION-SC-08-1; duplicating it in the release-gate added no coverage and required registry-checkout wiring that the release workflow intentionally does not carry.

- **`tests/conformance/conftest.py`** — `_discover_registry_root()` gains a 4th fallback to the vendored `tokenpak/_tip_schemas/` tree shipped in the wheel (SC-07). Layer A + manifest + self-capability tests now run standalone in any installed environment. New helper `installed_validator_knows_schema(name)` + `pytest.mark.skipif` gates on Layer B + the Layer-C journal smoke: tests that depend on schemas added after the pinned PyPI validator's release skip gracefully instead of failing (mirrors the SC-07 runner's WARN convention).

The SC-08 CI path (registry-editable install) still has every schema; the skip never fires there. All 28 conformance tests pass on the canonical CI gate.

### Everything else from v1.3.3 content

All TIP-SC phase deliverables from v1.3.3 are present unchanged. See the [v1.3.3 CHANGELOG section](CHANGELOG.md) for the full list: ConformanceObserver, emission sinks at five chokepoints, LoopbackProvider, canonical manifests, 28-test pytest suite, `tokenpak doctor --conformance`, `tip-self-conformance.yml` workflow, capability refresh.

### Upgrade

```bash
pip install --upgrade tokenpak
tokenpak --version                    # 1.3.4
tokenpak doctor --conformance         # verify self-conformance
```

Users who were waiting on 1.3.3 should install 1.3.4 directly; 1.3.3 was never published to PyPI.
