# tokenpak v1.3.5

## TIP-1.0 self-conformance — release-gate hotfix-2

v1.3.5 recovers from two consecutive burned tag attempts (v1.3.3, v1.3.4). Content is the same as v1.3.3/v1.3.4 plus three narrowly-scoped release-gate fixes so the tag can finally ship to PyPI.

### What failed in v1.3.3 and v1.3.4

- **v1.3.3:** `release.yml`'s `test` step ran the full `pytest tests/` suite, which includes `tests/conformance/`. Those tests resolve TIP schemas via `tokenpak-tip-validator`; the release workflow did not check out `tokenpak/registry` or set `TOKENPAK_REGISTRY_ROOT`, so the tests failed with `schema.unavailable`.

- **v1.3.4 (fixes v1.3.3):** `--ignore=tests/conformance` was added to the release-gate pytest. But two latent bugs surfaced:
  - `Smoke test CLI` step ran `python -m tokenpak.cli --help`; `tokenpak.cli` is a package without `__main__.py`, so `-m` execution fails.
  - Python 3.12 matrix leg of `tip-self-conformance.yml` hit a `shutil.rmtree` race in `test_scenario_telemetry_row_validates`: Monitor.log's async SQLite writer thread still held files in the tempdir when the test context exited. 3.10 + 3.11 were tolerant; 3.12 raised `OSError`.

Both v1.3.3 and v1.3.4 are burned attempts retained on history for auditability. Neither published to PyPI; neither created a GitHub Release page.

### Fixes in v1.3.5

- **Smoke test CLI invocation:** `python -m tokenpak.cli --help` → `tokenpak --help`. Uses the installed console-script entry point, which is what end users actually run.
- **Layer A tempdir race:** both `tempfile.TemporaryDirectory()` call sites in `tests/conformance/test_layer_a_pipeline.py` use `ignore_cleanup_errors=True`. The observer row (the assertion target) is captured synchronously before teardown; the disk artifact is incidental here, so tolerating a cleanup race is correct.
- **`workflow_dispatch` trigger on `release.yml`**: allows manual preflight of the release workflow against any candidate commit before cutting a tag. The `release` + `publish` jobs are guarded by `if: github.event_name == 'push'` so dispatch runs never create a GitHub Release or upload to PyPI. Intended use:

  ```bash
  gh workflow run release.yml --ref <commit-sha> --repo tokenpak/tokenpak
  ```

  This verifies test + build pass before the real tag fires the full path.

### Everything else from v1.3.3/v1.3.4

All TIP-SC phase deliverables are present unchanged. See the [v1.3.3 CHANGELOG section](CHANGELOG.md) for the full list: ConformanceObserver, emission sinks at five chokepoints, LoopbackProvider, canonical manifests, 28-test pytest suite, `tokenpak doctor --conformance`, `tip-self-conformance.yml` workflow, capability refresh.

### Upgrade

```bash
pip install --upgrade tokenpak
tokenpak --version                    # 1.3.5
tokenpak doctor --conformance         # verify self-conformance
```

Users who were waiting on 1.3.3 or 1.3.4 should install 1.3.5 directly; neither prior version was published to PyPI.

### Preflight proof

This release was preflight-verified via `gh workflow run release.yml --ref <commit>` before the `v1.3.5` tag was pushed. The preflight run exercised test + build without creating a Release or publishing; the tag push then fired the full path test → build → release → publish.
