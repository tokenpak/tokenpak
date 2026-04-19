---
title: TokenPak Rollback and Recovery Runbook
type: standard
status: draft
depends_on: [11-release-workflow-overview.md, 14-production-deployment-runbook.md, 15-post-deploy-validation.md]
---

# TokenPak Rollback and Recovery Runbook

What to do when a TokenPak release is broken in production. PyPI is near-immutable by design, so TokenPak's rollback model is **supersede, don't yank** — with narrow exceptions.

This document answers three questions: *when to act*, *who decides*, and *exactly what to run*.

---

## 1. Rollback Triggers

Any one of these conditions triggers rollback evaluation. The decider (§3) makes the call.

- **Critical:** shipped release installs but errors on the demo path or breaks a documented CLI verb for most users.
- **Critical:** shipped release alters request/response bytes on a byte-preserved passthrough path (Constitution §5.2).
- **Critical:** shipped release leaks credentials, PII, or prompt content into logs, telemetry, or the dashboard.
- **Critical:** shipped release bricks an existing user's `~/.tokenpak/` state on upgrade.
- **High:** savings number regresses by >20% on the reference benchmark.
- **High:** release breaks a documented integration (`tokenpak integrate <client>`) for a shipped client.
- **High:** security finding (new bandit High; dependency with a published CVE).
- **Medium (watch, don't immediately act):** performance regression under 20% on the hot path; non-critical user-visible bug.

## 2. Severity Thresholds — Action Mapping

| Trigger | Default action | Communication |
|---|---|---|
| Critical | Supersede within 2 hours. Yank the broken release if it corrupts user state or leaks credentials. | 18 §Rollback — immediate. |
| High | Supersede within 24 hours. | 18 §Patch planned. |
| Medium | Patch in the next scheduled release. | 18 §Known issue. |

"Supersede" means publish a new patch release whose version is strictly higher than the broken one, and update documentation and `:latest` container tag to point at the new version.

"Yank" means mark the broken release as unavailable on PyPI. **Use sparingly** — yanks break users who pinned to that version; prefer supersede.

## 3. Rollback Decision Owner

The rollback decider is named in the release log (19) for each release. Typically the release owner, unless explicitly delegated before the release.

Escalations:

- If the decider is not available within 30 minutes of a Critical trigger, any repo maintainer can execute §4 with a note in the release log.
- If the organization has multiple maintainers and the decider is offline during a Critical event: escalate by message; start a supersede prep regardless; the decider can veto on return.

## 4. Rollback Commands / Process

### 4.1 Supersede (default path)

This is almost always the right action.

```bash
# 1. Create a fix branch from the problematic tag
git checkout -b fix/release-X.Y.Z-critical vX.Y.Z

# 2. Apply the fix (minimal diff — don't bundle other work)
# ...edit...
git add -- <files>
git commit -m "fix: <one-line>"

# 3. Merge fix to main via PR (fast review path acceptable for Critical)
git push github fix/release-X.Y.Z-critical
# ...open PR, merge to main...

# 4. Tag the superseding release
git checkout main
git pull
git tag -a vX.Y.(Z+1) -m "tokenpak X.Y.(Z+1) — rollback supersede for X.Y.Z"
git push github vX.Y.(Z+1)

# 5. Run through 14-production-deployment-runbook.md §5–§6
# (Staging validation is abbreviated for Critical supersedes — 13 §Hotfix mode, see 17.)

# 6. After publish, update docs to recommend the new version
```

The broken release stays on PyPI (users who pinned to it keep working). The new patch supersedes it.

### 4.2 Yank (narrow — credentials/state corruption only)

Only use yank if the broken release actively harms users who install it. Examples: leaks credentials, corrupts their data, downloads malware. A plain bug does not qualify.

```bash
# 1. Decide you need to yank (see §2 triggers). This is irreversible-ish:
# yanked versions stay installable via explicit `==X.Y.Z` pin but are
# hidden from resolvers, so this is intentional user-disruption.

# 2. Yank using PyPI web UI: https://pypi.org/manage/project/tokenpak/release/X.Y.Z/
#    -> click "Yank" -> provide reason (max 256 chars) -> confirm

# 3. Alternatively via the PyPI JSON API (with an authorized token):
# curl -X POST https://pypi.org/pypi/tokenpak/X.Y.Z/yank \
#      -H "Authorization: token <pypi-token>" \
#      -d "reason=<short-reason>"

# 4. Simultaneously publish the superseding patch via §4.1.
```

After a yank:

- The GitHub release for `vX.Y.Z` gets a prominent "Yanked: see vX.Y.(Z+1)" banner.
- Communication (18) goes out on the same 30-minute window as publish.
- The yank is logged in the release log entry (19) with the reason text verbatim.

### 4.3 Container image rollback

```bash
# 1. If :latest was pointed at the broken image, repoint it at the previous good tag
docker pull tokenpak:<previous-good-version>
docker tag tokenpak:<previous-good-version> tokenpak:latest
docker push tokenpak:latest

# 2. The broken image tag stays (like PyPI, images are immutable).

# 3. Once the supersede is built, retag :latest to the supersede.
```

### 4.4 Docs / README rollback

If the broken release's docs have been merged to `main`:

- Do not revert docs on `main` unless the docs themselves contain dangerous or misleading instructions.
- Update the README version badge back to the previous good version.
- Add a note to the deprecated `X.Y.Z` docs pointing at the supersede.

## 5. State / Schema Considerations

TokenPak's only persistent state is `monitor.db` and `companion/journal.db`. Rollback rules:

- **If the broken release introduced a schema migration that runs on install/upgrade:** the supersede must ship a forward migration compatible with both the pre- and post-broken-release schema. Never assume users will re-install clean.
- **If the broken release corrupts DB state:** the supersede includes a repair step (documented in release notes and in a troubleshooting page under `docs/troubleshooting/`).
- **Users downgrading manually** (`pip install tokenpak==X.Y.(Z-1)`) must get a working tool. If schema-forward migration breaks the previous schema, document the manual downgrade path in the release notes.

## 6. Recovery Validation

After the supersede ships, run `15-post-deploy-validation.md` against the *supersede* release. Explicitly include:

- [ ] The specific check that originally failed is now passing.
- [ ] Users who had the broken release can upgrade (`pip install --upgrade tokenpak`) without manual intervention.
- [ ] The `cache_origin`/`monitor.db` semantics changed by the broken release are restored.
- [ ] No new regressions introduced by the fix.

## 7. Incident Documentation

Every rollback is an incident. Recorded as a section in the release log (19):

- **Trigger time** — when the problem became apparent.
- **Detection source** — who/what noticed (the watch period, a user report, CI).
- **Severity** — Critical/High/Medium using §1 language.
- **Decider** — who signed off on rollback.
- **Action** — supersede, yank, both, or wait.
- **Timeline** — trigger → decision → fix merged → supersede tag → supersede PyPI publish → supersede validated.
- **Root cause** — one paragraph. What failed; what gate didn't catch it; why.
- **Fix** — the commit SHA of the fix.
- **Follow-up** — gate changes, test additions, doc updates to prevent recurrence. Opened as issues with the release `X.Y.Z` referenced.

## 8. Communication During Rollback

Follow `18-release-communication-template.md §Rollback notice`. Post within 30 minutes of the decision, not the trigger. Update at fix-merged, supersede-published, and validation-complete.

Silence is not an option. If you decide not to act on a trigger, say so (§Known issue template).

## 9. Never-Rollback Rules

Some actions look like rollback but aren't allowed:

- **Do not delete the broken release's git tag.** History stays.
- **Do not force-push `main` to hide the broken commit.** The commit stays.
- **Do not delete the GitHub release.** Mark it yanked if applicable.
- **Do not edit the broken release's PyPI description.** Let it stand.
- **Do not re-upload to PyPI under the same version.** PyPI rejects it; more importantly, this would be a silent change.

The goal is a transparent history of what went wrong and what replaced it, not to pretend the event didn't happen.

## 10. Evidence to capture

In the release log entry (19):

- Exact command outputs from §4 (tag push, twine upload, yank if used, container retag).
- Before/after `tokenpak --version` on a reference machine.
- Before/after `tokenpak demo` panel.
- PyPI JSON endpoint response for both the broken and supersede versions.
- Communication artifacts (posts, emails) with timestamps.

## 11. Related

- `14-production-deployment-runbook.md` — how the broken release got out in the first place; often the fix lives in adding a gate.
- `15-post-deploy-validation.md` — what should have caught it.
- `17-hotfix-workflow.md` — the workflow for the supersede itself.
- `18-release-communication-template.md` — what to say, when.
- `19-release-log-template.md` — where all of this gets recorded.
