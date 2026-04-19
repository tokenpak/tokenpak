---
title: TokenPak Hotfix Workflow
type: standard
status: draft
depends_on: [11-release-workflow-overview.md, 13-staging-validation-checklist.md, 14-production-deployment-runbook.md, 16-rollback-and-recovery-runbook.md]
---

# TokenPak Hotfix Workflow

The shortcut path for urgent production fixes. A hotfix trades some gates for speed — so the gates it keeps are non-negotiable.

This document defines when the hotfix path is allowed and what the shortcut actually shortens.

---

## 1. Hotfix Definition

A hotfix is a patch release that:

- Supersedes a broken production release via `16-rollback-and-recovery-runbook.md §4.1`, AND
- Cannot wait for the full staging workflow (minutes–hours matter), AND
- Is scoped to the minimum diff that fixes the trigger (no bundled improvements).

A release that doesn't meet all three criteria is a normal patch release. Run the full workflow.

## 2. When the Hotfix Path Is Allowed

Exactly the Critical triggers from `16 §1`:

- Shipped release errors on the demo path or breaks a documented CLI verb for most users.
- Shipped release alters bytes on a byte-preserved passthrough path.
- Shipped release leaks credentials, PII, or prompt content.
- Shipped release bricks existing user state.

Plus specifically these High triggers:

- Published CVE in a pinned dependency that affects TokenPak at runtime.
- Regression that makes a documented client integration non-functional.

High triggers not listed above and all Medium triggers go through the normal patch process, not hotfix.

## 3. Hotfix Approval Rules

- **Release owner** makes the call. If unavailable, any repo maintainer can invoke hotfix mode with a note in the release log.
- **Reviewer** is required even in hotfix mode. If only the release owner is available, they request asynchronous review and ship in parallel; the reviewer's sign-off goes in the release log within 24 hours.
- **Written rationale** in the release log (19) — one paragraph answering "what broke, how we found it, why it must ship now, what is in the diff."

No hotfix ships without the written rationale logged. A one-line commit message is not enough.

## 4. Required Validation Minimums

The hotfix path shortens validation. It does **not** eliminate it.

### 4.1 Retained from the full workflow (non-negotiable)

- A1 — Test suite green on the fix branch.
- A5 — Fresh-machine install test against the Test PyPI build.
- A7 — Byte-fidelity test, if the fix touches the passthrough path.
- B1 — Automated audit clean.
- The specific regression test that protects against this fix's failure mode landing again (add it in the same PR).

### 4.2 Shortened

- 13 (staging validation) runs in **hotfix mode**: only §1 Build/install, §2 Startup, §4 Proxy core flow, §8 Demo path, §10 Regression. Skipped sections are listed in the release log with "deferred to next full release" notes.
- 15 (post-deploy validation) watch period reduced to **1 hour** for hotfixes (vs default 4 hours for patches).

### 4.3 Dropped

- 13 §9 Upgrade-path validation — dropped for hotfixes where the fix does not touch installed-state schemas or integrations.
- Container image publish — can wait for the next full release if the hotfix is PyPI-only and no deployment target is blocking on it.

If any "dropped" category was actually needed (schema touched, deployments blocked), the hotfix path was wrong; stop and run the full workflow.

## 5. Deployment Path

This is the condensed variant of `14-production-deployment-runbook.md`.

```bash
# 1. Branch from the broken tag
git checkout -b hotfix/X.Y.(Z+1)-<slug> vX.Y.Z

# 2. Apply minimum diff + regression test
# ...edit...
git add -- <fix files> <regression test file>
git commit -m "fix: <one-line trigger description>"

# 3. Open PR; request review; merge to main (fast path)
git push github hotfix/X.Y.(Z+1)-<slug>

# 4. On main after merge:
git checkout main
git pull github main
git tag -a vX.Y.(Z+1) -m "tokenpak X.Y.(Z+1) — hotfix: <trigger>"
git push github vX.Y.(Z+1)

# 5. Test PyPI publish (fast):
python -m build
twine upload --repository testpypi dist/*

# 6. Hotfix-mode staging validation (13 §4.2 subset)

# 7. PyPI publish:
twine upload dist/*

# 8. Verify & start shortened watch (15 §8, 1 hour)
```

Every step timestamp goes in the release log.

## 6. Post-Hotfix Follow-Up

Hotfixes incur debt. The follow-up is mandatory, not optional.

Within **7 days** of the hotfix:

- [ ] Every dropped/shortened validation step from §4.3 is re-run against the hotfix release. Any finding triggers a follow-up fix in the next scheduled release.
- [ ] The dropped steps are re-added to the next full release's validation.
- [ ] The root cause analysis is posted in the release log (19 §RCA). Includes which gate should have caught this and why it didn't.
- [ ] If the RCA identifies a gate gap, the gate is updated in `10-release-quality-bar.md`, `13`, or `09-audit-rubric.md` in a separate PR titled `standards: close gate gap from vX.Y.(Z+1)`.
- [ ] If the fix introduced a regression test (it should), the test remains in the main test suite after the hotfix.

## 7. Back-Merge / Branch Reconciliation

- The hotfix branch `hotfix/X.Y.(Z+1)-<slug>` merges to `main` as part of §5 step 3. No separate back-merge needed if `main` is the only production branch.
- If, in the future, TokenPak adopts `release/*` branches for sustained minor-version support, the hotfix must be cherry-picked to each live release branch. The release log captures every cherry-pick SHA.

Currently there are no release branches. This section is forward-looking for when that changes.

## 8. What a Hotfix Is Not

- **Not a feature shortcut.** New features never use the hotfix path, regardless of pressure.
- **Not a version-bump-only release.** A hotfix that doesn't fix the named trigger is blocked.
- **Not an opportunity to bundle.** One trigger = one hotfix. Two triggers that happen to be open at the same time = two hotfixes (or one if they are clearly the same root cause).
- **Not an excuse to skip the release log.** Hotfix release-log entries are more detailed, not less.

## 9. Evidence to capture

In the release log entry (19) — same structure as a normal release, plus:

- The written rationale paragraph (§3).
- A pointer to the trigger (original release log entry row for `vX.Y.Z`).
- Before/after the specific failing behavior.
- A pointer to the follow-up items with opened-issue links (§6).

## 10. Related

- `16-rollback-and-recovery-runbook.md` — hotfix is the deploy half of supersede.
- `14-production-deployment-runbook.md` — the full workflow this document shortens.
- `13-staging-validation-checklist.md` — reference the "hotfix mode" subset in §4.2 above.
- `18-release-communication-template.md` §Hotfix — the message templates.
