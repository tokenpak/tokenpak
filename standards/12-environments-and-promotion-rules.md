---
title: TokenPak Environments and Promotion Rules
type: standard
status: draft
depends_on: [11-release-workflow-overview.md]
---

# TokenPak Environments and Promotion Rules

How code moves from a developer's checkout to a user's `pip install`. Read 11 first; this document is the rules for the arrows in that diagram.

---

## 1. Environment Definitions

TokenPak is a local tool. "Environment" means **release channel** — where a particular build of the code is made available — plus the machine that runs it. No TokenPak-operated cloud service is part of this definition.

### 1.1 Local

- **What:** A developer's checkout at `/home/<user>/tokenpak/` (or wherever).
- **Install:** `pip install -e .` in a virtual environment.
- **Source of truth:** Whatever the developer has committed (or not). May diverge from `main`.
- **Allowed to:** Anything — experiment, break things, run against real or fake providers.
- **Not allowed to:** Represent TokenPak to anyone else.

### 1.2 Dev / CI

- **What:** The current tip of `main` on GitHub, exercised by CI.
- **Install:** CI job creates a fresh venv, runs `pip install .` and the test matrix.
- **Source of truth:** `main` branch.
- **Allowed to:** Validate every commit and PR. Produce coverage, benchmark, and audit artifacts.
- **Not allowed to:** Publish packages. Modify production state. Push tags.

### 1.3 Staging / RC

- **What:** A release candidate build from a tag matching `v*rc*` (e.g., `v1.3.0rc1`), published to Test PyPI.
- **Install:** `pip install -i https://test.pypi.org/simple/ tokenpak==1.3.0rc1`.
- **Source of truth:** The exact commit the `rc` tag points at. Never re-point an `rc` tag.
- **Allowed to:** Be installed on clean validation machines for the staging checklist. Collect aggregate telemetry from opt-in reviewers.
- **Not allowed to:** Be presented to users as a production release. Test PyPI explicitly states builds there are not stable.

### 1.4 Production (PyPI)

- **What:** A tagged release at [pypi.org/project/tokenpak](https://pypi.org/project/tokenpak/).
- **Install:** `pip install tokenpak` (latest) or `pip install tokenpak==X.Y.Z`.
- **Source of truth:** The exact commit the `vX.Y.Z` tag points at.
- **Allowed to:** Be installed by anyone. Referenced from docs, README, announcements.
- **Not allowed to:** Have its artifact altered after publish. If broken, publish a patch, don't re-upload.

### 1.5 Container images (optional)

- **What:** OCI images built from a tagged commit, published to a container registry consumed by the configs in `deployments/`.
- **Tag scheme:** `tokenpak:X.Y.Z` and `tokenpak:latest` pointing at the most recent production release.
- **Source of truth:** The same git tag as the PyPI release; images are a secondary distribution format, never ahead of PyPI.
- **Allowed to:** Be referenced from the self-hosting READMEs in `deployments/`.
- **Not allowed to:** Diverge in version from PyPI. Skip a PyPI release.

### 1.6 Development mirror (optional, project-specific)

- **What:** A private git repository that holds the continuously-integrated work-in-progress trunk. The public git repo receives only release commits. See `21 §9` for the full specification.
- **Source of truth:** Its own `main` branch. Diverges from the public repo's `main` between releases by design; reconverges at each release via the `21 §9.4` promotion sequence.
- **Allowed to:** Accept continuous development pushes. Host CI workflows (`.github/workflows/`). Carry fleet/internal commit identities. Accept force-pushes.
- **Not allowed to:** Be referenced from user-facing docs. Contain PyPI artifacts. Serve as an install channel for anyone outside the maintainer group.

Projects that do not adopt this pattern treat every reference to the development mirror as a no-op — the remaining four channels (Local, Dev/CI, Staging/RC, Production) work the same way.

## 2. Allowed Actions Per Channel

| Action | local | dev/CI | dev mirror | staging/RC | production |
|---|---|---|---|---|---|
| Install from checkout | ✓ | — | — | — | — |
| Install from Test PyPI | — | — | — | ✓ | — |
| Install from PyPI | — | — | — | — | ✓ |
| Run against real provider credentials | ✓ (your own) | ✗ (CI secrets only) | ✓ (maintainer creds) | ✓ (validation creds) | ✓ (user creds) |
| Modify monitor DB / state | ✓ | ✓ (test only) | ✓ (maintainer only) | ✓ (validation only) | ✓ (user's own) |
| Publish a PyPI artifact | ✗ | ✗ | ✗ | ✗ (Test PyPI only) | ✓ |
| Push a tag | ✗ | ✗ | ✓ (dev tags only) | ✓ (rc tag) | ✓ (release tag) |
| Accept force-push | ✓ (local branches only) | ✗ (CI is read-only) | ✓ | ✗ | ✗ |
| Carry fleet / internal commit identities | ✓ | ✗ | ✓ | ✗ | ✗ |
| Be referenced from README | ✗ | ✗ | ✗ | ✗ | ✓ |

## 3. Promotion Criteria

### 3.1 Local → dev/CI

- Committed to a branch.
- Pushed to GitHub.
- PR opened against `main`.

CI runs on every push. Promotion to `main` requires:

- All A Technical gates from `10-release-quality-bar.md` green on CI.
- At least one human review (waivable for routine patches per 11 §4).
- Squash merge or clean linear history.

### 3.2 dev/CI → staging/RC

Promotion to staging/RC requires:

- A tagged commit on `main`: `git tag vX.Y.ZrcN -m "…"`; `git push --tags`.
- Build step: `python -m build` produces sdist + wheel.
- Publish step: `twine upload --repository testpypi dist/*`.
- Install from Test PyPI on a clean venv to verify the published artifact.
- Staging validation checklist (`13`) starts.

### 3.3 staging/RC → production

Go / no-go per `11 §7`. All answers yes. Then:

- **If the project uses a development mirror (`§1.6`):** first run the staging→production promotion per `21 §9.4`. That sequence produces a single release commit on the public `main` under the `TokenPak <hello@tokenpak.ai>` identity and pushes the tag. The steps below are then absorbed by §9.4 and must not be run a second time.
- **If the project does not use a development mirror:** the traditional flow applies:
  - Tag: `git tag vX.Y.Z -m "…"`; `git push --tags`.
  - Build: `python -m build`.
  - Publish: `twine upload dist/*`.
  - Verify with a clean-venv install from real PyPI.
  - Post-deploy validation (`15`) starts.

In both cases the artifact promoted to PyPI is built from the `vX.Y.Z` tag and not from a branch head.

### 3.4 production → containers

- Build images from the exact `vX.Y.Z` tag.
- Tag as `tokenpak:X.Y.Z` and retag `tokenpak:latest` (latest only for stable releases, not pre-releases).
- Push to the registry referenced by `deployments/`.
- Update `deployments/*/README.md` examples if the image tag format changed (it shouldn't).

## 4. Config Management Rules

- **One config chain** per `01-architecture-standard.md §6`. No environment-specific config files in the package. Behavior is controlled by env vars (`TOKENPAK_*`) and the user's own `~/.tokenpak/config.yaml`.
- **No per-environment code paths.** If a feature behaves differently in CI vs production, that's a bug unless explicitly designed (e.g., telemetry off in CI by default).
- **No environment gating in the release.** A release either passes or fails; there's no "we'll fix the prod config after we ship."

## 5. Secrets Rules

- **Never commit secrets.** Pre-commit hook + audit grep cover this.
- **CI secrets** live in GitHub Actions secrets (once CI exists). Scoped per workflow. Documented in `14 §Secrets`.
- **Release publishing** uses a PyPI API token scoped to the `tokenpak` project, stored as a GitHub secret. Never a personal token.
- **Test PyPI + PyPI** use separate tokens. Never re-use one for the other.
- **Rotation:** PyPI tokens rotate on a schedule (every 180 days) or on any suspicion of compromise.
- **No secrets in release artifacts.** Confirmed by B3 gate (forbidden-phrase scan) plus a secrets scanner in CI (follow-up).

## 6. Data Handling Rules

- **No TokenPak-operated databases.** `monitor.db` is always on the user's machine.
- **Test data stays in `tests/` or `examples/`.** Never in the root.
- **Benchmark fixtures are synthetic.** Real customer prompts never enter the repo.
- **Coverage and benchmark artifacts** (`coverage.json`, `benchmark.json`) are regenerated by CI, not edited by hand.

## 7. Environment Drift Policy

Drift appears when the environments disagree on what TokenPak is or does. The four classes we watch for:

- **Identity drift** — README, site, dashboard, or docs describe TokenPak differently. Constitution §2 is the one sentence; audit rubric 09 §3.6 checks this.
- **Version drift** — Released version, `setup.py` / `pyproject.toml` version, `CHANGELOG.md` header, Docker image tag, and the most recent git tag must all match before a release closes.
- **Dependency drift** — CI's pinned dependency set and the release artifact's metadata agree. Lockfile changes land in their own PR.
- **Automation drift** — Manual release steps must match the automated equivalents when CI catches up. Every time you run a step by hand, update the runbook if it's out of date.

Drift is a release blocker per 10 §B. The audit rubric (09) surfaces it; this document prescribes that it get fixed in-release, not deferred.
