# tokenpak v1.3.6

## TIP-1.0 self-conformance — release-gate hotfix-3 (pin validity + preflight coverage)

v1.3.6 recovers from three consecutive release attempts that failed before publication (v1.3.3, v1.3.4, v1.3.5). Content is identical to those three attempts plus two narrowly-scoped fixes — one correcting the actual failure, one extending the preflight so the same bug class cannot burn a tag again.

### What failed in v1.3.5

v1.3.5's `Release TokenPak` workflow got past `Run Tests` + `Build Distribution` (both green), then failed at the `Create GitHub Release` job's `Set up job` step:

```
Unable to resolve action actions/download-artifact@fa0a91b85d4f404e444306234a2a8284e0a91ef9,
unable to find version fa0a91b85d4f404e444306234a2a8284e0a91ef9
```

Two of the five SHA pins I ported from `publish.yml` to `release.yml` during F-01 were **invalid values** — not the actual published tag SHAs. Verified against the upstream repos via `git ls-remote --tags`:

| Action | Pinned (invalid) | **Actual upstream SHA** |
|---|---|---|
| `actions/download-artifact` (v4.1.8) | `fa0a91b85d4f404e444306234a2a8284e0a91ef9` | **`fa0a91b85d4f404e444e00e005971372dc801d16`** |
| `pypa/gh-action-pypi-publish` (v1.12.3) | `76f52bc884231f62b3a5c8ae4dab2bd50e2e5720` | **`67339c736fd9354cd4f8cb0b744f2b82a74b5c70`** |

The three other pins (`actions/checkout v4.2.2`, `actions/setup-python v5.3.0`, `actions/upload-artifact v4.6.0`) are valid — they were exercised and passed on v1.3.5's test + build jobs.

The v1.3.5 preflight did not catch this because `release` + `publish` jobs are gated by `if: github.event_name == 'push'` — they are skipped on `workflow_dispatch`, so the SHA pins used only in those jobs were never resolved during preflight.

### Fixes in v1.3.6

- **SHA pins corrected** in `release.yml` — both invalid SHAs replaced with the actual upstream values. Verified with `git ls-remote --tags`.

- **New `validate-pins` preflight job** in `release.yml`. Runs before `test` on both `push:tags` and `workflow_dispatch`. Uses `git ls-remote --tags <repo> refs/tags/<tag>` (with `^{}` peel for annotated tags) to resolve every SHA-pinned action to its upstream commit and fails fast on any divergence. Closes the gap that let v1.3.5 burn — SHA pins for `release` + `publish` steps are now validated during preflight even though those jobs themselves are dispatch-skipped.

### Preflight verified

This release was preflight-verified via `gh workflow run release.yml --ref <commit>` before the `v1.3.6` tag was pushed. The preflight run exercised:

1. `validate-pins` — all 5 SHAs resolved and matched their expected commits.
2. `test` — release-gate tests passed (with `tests/conformance/` excluded; canonical gate is `tip-self-conformance.yml`).
3. `build` — wheel + sdist built, `twine check` passed, SHA256SUMS generated, artifacts uploaded.

`release` + `publish` jobs were skipped on preflight per design (only real tag pushes cut a GitHub Release or upload to PyPI).

### Why three burned tags

| Tag | Failed at | Class closed by |
|---|---|---|
| v1.3.3 | `test` step — `tests/conformance/` couldn't resolve registry schemas | v1.3.4 (`--ignore=tests/conformance`) |
| v1.3.4 | `Smoke test CLI` — `python -m tokenpak.cli` (no `__main__.py`) + Python 3.12 tempdir race | v1.3.5 (`tokenpak --help` + `ignore_cleanup_errors=True`) |
| v1.3.5 | `Create GitHub Release` — invalid `download-artifact` SHA pin | v1.3.6 (pin fix + `validate-pins` preflight) |

Each fix closed a class; v1.3.6 adds the preflight that would have caught the SHA class before any of them. v1.3.3, v1.3.4, v1.3.5 are release attempts that failed before publication; tags retained for auditability.

### Everything else from v1.3.3 content

All TIP-SC phase deliverables are present unchanged. See the [v1.3.3 CHANGELOG section](CHANGELOG.md) for the full list: ConformanceObserver, emission sinks at five chokepoints, LoopbackProvider, canonical manifests, 28-test pytest suite, `tokenpak doctor --conformance`, `tip-self-conformance.yml` workflow, capability refresh.

### Upgrade

```bash
pip install --upgrade tokenpak
tokenpak --version                    # 1.3.6
tokenpak doctor --conformance         # verify self-conformance
```

v1.3.3, v1.3.4, and v1.3.5 were never published to PyPI. Users should install v1.3.6 directly.
