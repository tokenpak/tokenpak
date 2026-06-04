---
---

Release-gate: tag-source ancestry check.

`release.yml` now validates, as the first step of the `build` job, that a pushed
`v*` tag is reachable from `origin/main` before any artifact is built. On a tag that
is not on `origin/main` the workflow fails fast (`::error::` + `exit 1`), preventing a
wrong-source release from drifting onto PyPI. The check is skipped for `rehearsal-*`
tags (excluded by the job-level `refs/tags/v*` guard) and for `workflow_dispatch`
preflight runs (step-level `github.event_name == 'push'` guard).

No version impact: CI workflow definition only — no public API, runtime, or packaged
code changes. Enforces the staging → public promotion path before tagging.
