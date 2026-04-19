---
title: TokenPak Production Deployment Runbook
type: standard
status: draft
depends_on: [11-release-workflow-overview.md, 12-environments-and-promotion-rules.md, 13-staging-validation-checklist.md]
---

# TokenPak Production Deployment Runbook

The step-by-step procedure to publish a TokenPak release to PyPI (and optionally to the container registry). This document is operational — no philosophy beyond the preconditions.

Run these steps exactly, in this order. If a step fails, stop and consult §9 Escalation.

---

## 1. Purpose

Publish a tagged, staging-validated release of TokenPak such that `pip install tokenpak==X.Y.Z` works for all users within minutes of the PyPI push.

## 2. Scope

Covers: PyPI publish, container publish, git tag push, GitHub release creation, docs update.

Does not cover: code changes, test validation (that's 10/13), post-deploy health checks (that's 15).

## 3. Owner

The release owner named in the release log entry (19). Typically the PR author or the person who cut the RC tag.

## 4. Preconditions

Every one of these must be true before starting §6. Check them in order; stop if any fails.

- [ ] `11 §7` go / no-go answered yes on all six questions, recorded in the release log.
- [ ] Staging validation checklist (13) complete; every row has evidence; all sign-offs present.
- [ ] RC tag `vX.Y.ZrcN` exists on GitHub and points at the same commit that is going to production.
- [ ] Target version string `X.Y.Z` chosen per SemVer (patch/minor/major) and not previously used on PyPI.
- [ ] `setup.py` (or `pyproject.toml` when migrated) `version` field matches `X.Y.Z`.
- [ ] `CHANGELOG.md` has a `## [X.Y.Z] - YYYY-MM-DD` entry populated using `19-release-log-template.md` content.
- [ ] Rollback runbook (16) is current for the kind of changes in this release.
- [ ] A reviewer named in the release log is available for the next hour.
- [ ] Credentials — PyPI token, container registry token — are present in the environment this runbook runs in (see §7 Secrets).
- [ ] You are on the `main` branch, up to date with `github/main`, no uncommitted changes.
- [ ] **If the project uses a development mirror (12 §1.6):** the staging→production promotion per `21 §9.4` is complete. Public `main` HEAD is the release commit authored by `TokenPak <hello@tokenpak.ai>`; the `vX.Y.Z` tag has already been pushed to `github`. The §6.1 tag step below is *already done* under this model and must be skipped (attempting to re-tag will fail). Otherwise: §6.1 runs as written.

## 5. Release artifact verification

Before the tag push that triggers production publish, verify the artifact one more time:

```bash
# Check out the exact commit being released
git fetch github
git checkout <SHA from staging>

# Verify tag doesn't already exist for the production version
git tag -l "vX.Y.Z"        # must return empty

# Build locally (this matches what Test PyPI built)
rm -rf dist/ build/
python -m build

# Validate
twine check dist/*         # all PASSED

# Inspect
ls -la dist/               # one sdist (.tar.gz), one wheel (.whl)
python -m zipfile -l dist/tokenpak-X.Y.Z-*.whl | head -20

# Install clean + smoke
python -m venv /tmp/tokenpak-release-verify
/tmp/tokenpak-release-verify/bin/pip install dist/tokenpak-X.Y.Z-*.whl
/tmp/tokenpak-release-verify/bin/tokenpak --version    # must print X.Y.Z
/tmp/tokenpak-release-verify/bin/tokenpak demo        # must print a savings panel
rm -rf /tmp/tokenpak-release-verify
```

All four smoke outputs must match expectations. If any does not, stop and consult §9.

## 6. Deployment Steps

Run these in order. Do not reorder. Do not improvise.

### 6.1 Tag the release

**Skip this step if the project uses the development mirror model** (`12 §1.6`, `21 §9`). Under that model the tag is pushed as the final step of the promotion sequence (`21 §9.4` step 5), so by the time §4 preconditions pass the tag already exists on `github`. Verify with:

```bash
git ls-remote --tags github | grep -E "refs/tags/vX.Y.Z$"   # must match
```

and move to §6.2.

**Otherwise** (single-repo projects):

```bash
git tag -a vX.Y.Z -m "tokenpak X.Y.Z"
git push github vX.Y.Z
```

Record the timestamp and tag SHA in the release log.

### 6.2 Publish to PyPI

```bash
# Assumes dist/ from §5 is still present and matches tag
twine upload dist/*
```

Record the upload timestamp in the release log.

Expected output: two "View at" URLs pointing at the new version on PyPI.

### 6.3 Verify PyPI

```bash
# PyPI CDN can take up to 5 min to reflect. Poll.
curl -fsSL "https://pypi.org/pypi/tokenpak/X.Y.Z/json" | jq -r '.info.version'
# must print X.Y.Z
```

Once the JSON returns the version, the package is globally installable.

### 6.4 Clean-venv install check

```bash
python -m venv /tmp/tokenpak-pypi-verify
/tmp/tokenpak-pypi-verify/bin/pip install tokenpak==X.Y.Z
/tmp/tokenpak-pypi-verify/bin/tokenpak --version
/tmp/tokenpak-pypi-verify/bin/tokenpak demo
rm -rf /tmp/tokenpak-pypi-verify
```

All outputs must match §5 expectations. If not, proceed immediately to `16-rollback-and-recovery-runbook.md`.

### 6.5 Create GitHub release

Using the GitHub UI or `gh release create`:

```bash
gh release create vX.Y.Z \
  --title "TokenPak X.Y.Z — <one-line summary>" \
  --notes-file RELEASE_NOTES.md
```

`RELEASE_NOTES.md` is the content filled out from `19-release-log-template.md`, stripped of internal-only sections.

### 6.6 Publish container images (if in scope)

Only if this release includes container updates and the image publish automation is present:

```bash
# Build with the exact tag
docker build -t tokenpak:X.Y.Z -t tokenpak:latest .

# Push to the registry referenced by deployments/
docker push tokenpak:X.Y.Z
docker push tokenpak:latest   # only for stable releases
```

Pre-releases (rc) do not get the `:latest` tag.

### 6.7 Docs site

If docs changed:

- If `mkdocs.yml` is active, the docs site rebuilds from `github/main`. Confirm the build succeeded (CI, when present; otherwise visual check).
- Update `tokenpak.ai` landing page if it references a specific version.

## 7. Config / secrets verification

Before the first `twine upload` of a session:

- [ ] `~/.pypirc` has the correct PyPI token entry, or `TWINE_USERNAME=__token__` + `TWINE_PASSWORD=<token>` are exported.
- [ ] Test PyPI token and PyPI token are different. (§ `12 §5`.)
- [ ] Container registry token, if publishing images, is present and scoped to this project.
- [ ] SSH to `github-tokenpak:tokenpak/tokenpak.git` works (§ `standards/` push rehearsal).

If any check fails, stop. Fix the credential situation, then restart §6.1 (or continue from the last completed step if already partway through).

## 8. Success criteria

A release is successful when all of:

- PyPI JSON endpoint returns `X.Y.Z`.
- Clean-venv `pip install tokenpak==X.Y.Z` + `tokenpak demo` passes on the operator's machine.
- GitHub release page exists for `vX.Y.Z`.
- Release log entry (19) has timestamps for tag push, PyPI publish, post-PyPI verify.
- Post-deploy validation (15) is started.

Any failure that makes a success-criterion false and cannot be resolved within 15 minutes escalates to §9.

## 9. Escalation path

Escalation order for a stuck or failed release:

1. **Release owner** pauses the runbook, records the stuck step and error text in the release log.
2. **Rollback decider** (may be the same person) evaluates whether to continue, wait, or roll back per `16-rollback-and-recovery-runbook.md`.
3. **If PyPI publish succeeded but something downstream is broken:** do not yank. Prepare a patch release that supersedes. See `16` §PyPI policy.
4. **If tag push succeeded but PyPI publish failed:** the tag stays; fix the publish issue and retry §6.2. Do not re-push the tag to a different SHA.
5. **If PyPI publish succeeded but the artifact is actually broken:** proceed to `16` to prepare a superseding patch immediately. Post a release communication (18) within 30 min.

## 10. Validation checklist (this runbook was followed)

After the deploy, before `15` starts, the release owner checks:

- [ ] No steps skipped.
- [ ] No steps reordered.
- [ ] Every checkbox has a paste of the observed output or a log URL.
- [ ] Timestamps captured in the release log for every §6 substep.
- [ ] The audit rubric (09) finds no new Critical or High issues post-deploy.

## 11. Evidence to capture

The release log entry (19) holds:

- Tag SHA + tag push timestamp.
- Build command output (or CI URL).
- `twine upload` output.
- PyPI JSON response after verify.
- Clean-venv install output.
- GitHub release URL.
- Container image digests (if published).
- Any errors encountered + resolutions.

## 12. Related docs

- `10-release-quality-bar.md` — the gates this deploy satisfies.
- `11-release-workflow-overview.md` — where this runbook sits in the pipeline.
- `12-environments-and-promotion-rules.md` — why the tag is what gets published.
- `13-staging-validation-checklist.md` — what ran before this.
- `15-post-deploy-validation.md` — what runs after.
- `16-rollback-and-recovery-runbook.md` — when this goes wrong.
- `18-release-communication-template.md` — what to say while running.
- `19-release-log-template.md` — where to record everything.
