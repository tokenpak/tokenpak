---
title: TokenPak Release Workflow Overview
type: standard
status: draft
depends_on: [00-product-constitution.md, 10-release-quality-bar.md]
pairs_with: [12-environments-and-promotion-rules.md, 13-staging-validation-checklist.md, 14-production-deployment-runbook.md, 15-post-deploy-validation.md, 16-rollback-and-recovery-runbook.md, 17-hotfix-workflow.md, 18-release-communication-template.md, 19-release-log-template.md]
---

# TokenPak Release Workflow Overview

This is the master release document. Every TokenPak release passes through the stages described here. Specific runbooks (12–19) implement each stage.

If a release step is missing from these documents, the release does not improvise — we update the documents first, then ship.

---

## 1. Objective

Ship TokenPak releases that are correct, documented, and reversible. Every release:

- Is buildable from a tagged commit.
- Installs cleanly on a fresh machine.
- Passes the quality bar in `10-release-quality-bar.md`.
- Has rollback instructions that work.

## 2. Release Philosophy

- **No tribal knowledge.** If a person has to "just know" a step, the document is incomplete.
- **Tagged artifacts only.** Production is built from a signed git tag, not a branch head.
- **Reversible beats fast.** A release that can be rolled back in five minutes is better than one that shipped an hour earlier.
- **Evidence, not assertion.** "Tests passed" means a log URL or a checked box with a timestamp, not a feeling.
- **Machine-agnostic.** No step depends on a specific operator's machine, credentials, or unwritten shortcuts.

## 3. Environments / Release Channels

TokenPak is a pip-distributed local tool plus optional container images. "Environment" here means **release channel**, not a deployed service.

| Channel | What it is | Who sees it | Install path |
|---|---|---|---|
| **local** | Your dev checkout | You | `pip install -e .` |
| **dev / CI** | The CI-tested tip of `main` | Contributors | CI matrix |
| **development mirror** *(project-specific)* | A private git repository that holds the work-in-progress trunk. Daily commits land here; the public repo receives only release commits. See `21 §9` for projects that adopt this pattern. | Maintainers | (git, not a user-facing install channel) |
| **staging / RC** | A release candidate built from a `rc*` tag, published to [Test PyPI](https://test.pypi.org) | Release reviewers | `pip install -i https://test.pypi.org/simple/ tokenpak==X.Y.ZrcN` |
| **production** | A tagged release on [PyPI](https://pypi.org/project/tokenpak/) | All users | `pip install tokenpak==X.Y.Z` |
| **container** (optional) | Container images corresponding to the tag, published to a registry referenced by `deployments/` | Users self-hosting | `docker pull …` / `kubectl apply -f deployments/k8s/` |

Details + promotion rules: [`12-environments-and-promotion-rules.md`](12-environments-and-promotion-rules.md). The **development mirror** is optional — projects that do not use a second git remote treat it as a no-op and skip the corresponding promotion step in `14 §4` preconditions.

## 4. Roles and Ownership

Small-team reality: many of these roles are the same person. The role still gets named because the *decision* is what matters, not the headcount.

| Role | Owns |
|---|---|
| **Release owner** | The go/no-go call for one specific release. Typically the PR author or the person cutting the tag. |
| **Reviewer** | At least one non-owner human review before production. For patch releases bundling only routine fixes, this can be satisfied by CI + the audit agent. |
| **Rollback decider** | The person empowered to yank or supersede. Same person as the release owner unless explicitly delegated before the release. |
| **Comms** | Whoever writes the release notes and announces. Usually the release owner. |

Role assignment for a specific release goes in the release log entry (19).

## 5. Standard Release Flow

The canonical pipeline:

**Ready for Staging → Staging Deploy → Staging Validation → Go / No-Go → Production Deploy → Post-Deploy Validation → Watch Period → Release Closeout**

Each stage has a document:

| Stage | Doc |
|---|---|
| 0. Change readiness | `10-release-quality-bar.md` gates A/B/C |
| 1. Staging deploy | `14` §Staging (build + test-PyPI) |
| 2. Staging validation | `13-staging-validation-checklist.md` |
| 3. Go / no-go | `11` §7 (this doc) |
| 4. Production deploy | `14-production-deployment-runbook.md` |
| 5. Post-deploy validation | `15-post-deploy-validation.md` |
| 6. Watch period | `15` §Watch |
| 7. Closeout | `19-release-log-template.md` entry completed |

Rollback path active the whole time: [`16-rollback-and-recovery-runbook.md`](16-rollback-and-recovery-runbook.md).

Hotfix path bypasses parts of this for urgent production fixes: [`17-hotfix-workflow.md`](17-hotfix-workflow.md).

## 6. Release Gates

The gates that block promotion are defined in `10-release-quality-bar.md` (A Technical / B Consistency / C Docs / D Messaging / E Operational). This workflow document defines *when* each gate is checked:

| Gate family | Checked at |
|---|---|
| A Technical (tests, install, mypy) | Stage 0 + Stage 1 |
| B Consistency (audit, forbidden phrases) | Stage 2 (staging validation) |
| C Documentation | Stage 0 + Stage 3 |
| D Messaging | Stage 3 |
| E Operational (rollback, compat) | Stage 3 + Stage 5 (post-deploy) |

## 7. Go / No-Go Decision Rules

Before promoting from staging to production, the release owner answers six questions. Any "no" blocks the release.

1. Do all A Technical gates pass on the tagged commit?
2. Does the staging validation checklist (13) have zero failed checks?
3. Is the release-notes draft (18, 19) complete and reviewed?
4. Is the rollback runbook (16) known to work for the kind of change this release makes?
5. Has the release owner confirmed no breaking changes slipped in unannounced?
6. Is there someone actively watching for at least the next hour?

The decision and its answers are recorded in the release log entry (19).

## 8. Release Types

| Type | Examples | Required runbooks |
|---|---|---|
| Patch | 1.2.1 → 1.2.2 | 13 (light), 14, 15, 19 |
| Minor | 1.2.x → 1.3.0 | All of 11–19 |
| Major | 1.x → 2.0 | All of 11–19 + prior announcement + migration guide |
| Hotfix | Urgent production fix | 17 (with 14/15/19 minimums) |

"Light" on the staging checklist (13) means a documented subset, not "skip it."

## 9. Exceptions

Exceptions to this workflow require all of:

1. A line in the release log (19) saying which step was skipped, why, who signed off.
2. A follow-up issue to repair whatever made the exception necessary.
3. The next release does not inherit the exception — it must go through the full workflow unless it also justifies an exception.

**Never-exception rules** (apply regardless):

- Production releases are built from a tag, never a branch.
- Rollback path is known before production deploy.
- Release log entry is created before the release, not after.

## 10. Linked Runbooks

- [12 — Environments and promotion rules](12-environments-and-promotion-rules.md)
- [13 — Staging validation checklist](13-staging-validation-checklist.md)
- [14 — Production deployment runbook](14-production-deployment-runbook.md)
- [15 — Post-deploy validation](15-post-deploy-validation.md)
- [16 — Rollback and recovery runbook](16-rollback-and-recovery-runbook.md)
- [17 — Hotfix workflow](17-hotfix-workflow.md)
- [18 — Release communication template](18-release-communication-template.md)
- [19 — Release log template](19-release-log-template.md)

## 11. Automation Status

At the time of drafting, TokenPak has **no CI/CD workflows** in `.github/workflows/` and no `Makefile` or `pyproject.toml` on `main`. This document describes the workflow as it *must be*; the immediate follow-up is to automate the manual pieces:

- CI: `tests on push` (lint, mypy on required subsystems, pytest fast suite).
- CI: `build artifact on tag` (sdist + wheel, upload to Test PyPI for `rc*` tags, PyPI for release tags).
- CI: `image publish on tag` (build container image referenced by `deployments/`).

Tracked as follow-up work; do not block the workflow itself on this automation. Manual runs still fit the workflow.
