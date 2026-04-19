---
title: TokenPak Release Log Template
type: standard
status: draft
depends_on: [11-release-workflow-overview.md, 13-staging-validation-checklist.md, 14-production-deployment-runbook.md, 15-post-deploy-validation.md]
---

# TokenPak Release Log Template

The release log is the audit trail for every TokenPak release. It lives at `docs/release-log/vX.Y.Z.md` — one file per release, kept forever.

Copy the block below into a new file at the start of every release, before any other release work.

The log is the single source of truth for who decided what, when, and with what evidence. If a decision isn't in the log, it didn't happen.

---

## Template

```markdown
---
title: TokenPak vX.Y.Z Release Log
version: X.Y.Z
release_type: <patch | minor | major | hotfix>
status: <planned | in-staging | in-production | watch-period | closed | rolled-back>
owner: <release owner>
reviewer: <reviewer>
rollback_decider: <name>
started: <UTC timestamp>
closed: <UTC timestamp or blank>
---

# TokenPak vX.Y.Z Release Log

## Scope

<One paragraph: what this release contains, at a level a future reader can understand without context.>

Linked work:
- <PR #N — title>
- <PR #N — title>
- <Issue #N — if tracking a larger initiative>

## Sign-offs

| Role | Name | Decision | Timestamp |
|---|---|---|---|
| Release owner | | | |
| Reviewer | | | |
| Rollback decider | | | |

## Stage 0 — Change readiness

Completed via `10-release-quality-bar.md` gates A/B/C.

- [ ] A Technical gates: <CI URL or paste>
- [ ] B Consistency gates: <audit output URL or paste>
- [ ] C Documentation gates: <notes>

## Stage 1 — Staging deploy

- **RC tag:** `vX.Y.ZrcN`
- **Tag SHA:** `<40-char>`
- **Tag timestamp:** `<UTC>`
- **Build output:** `<log or pasted summary>`
- **Test PyPI publish:** `<URL>`
- **Test PyPI publish timestamp:** `<UTC>`

## Stage 2 — Staging validation

Paste the completed checklist from `13-staging-validation-checklist.md`. Every row:
- owner
- pass/fail
- evidence (log URL, command output paste, or short note)

**Failed checks and resolutions:** <list or "none">

## Stage 3 — Go / no-go

| # | Question | Answer | Notes |
|---|---|---|---|
| 1 | Do all A Technical gates pass on the tagged commit? | | |
| 2 | Does the staging validation checklist (13) have zero failed checks? | | |
| 3 | Is the release-notes draft (18, 19) complete and reviewed? | | |
| 4 | Is the rollback runbook (16) known to work for the kind of change this release makes? | | |
| 5 | Has the release owner confirmed no breaking changes slipped in unannounced? | | |
| 6 | Is there someone actively watching for at least the next hour? | | |

Decision: <GO | NO-GO>
Decided by: <name>
Decided at: <UTC>

## Stage 4 — Production deploy

Following `14-production-deployment-runbook.md`.

| Substep | Timestamp | Output / URL |
|---|---|---|
| 6.1 Tag push | | |
| 6.2 PyPI upload | | |
| 6.3 PyPI JSON verify | | |
| 6.4 Clean-venv install check | | |
| 6.5 GitHub release created | | |
| 6.6 Container images (if applicable) | | |
| 6.7 Docs site refresh | | |

**Deployment issues encountered:** <list or "none">

## Stage 5 — Post-deploy validation

Paste the completed checklist from `15-post-deploy-validation.md`. Same shape as Stage 2 evidence.

## Stage 6 — Watch period

- **Watch start:** <UTC>
- **Watch length:** <duration — default 24h minor/major, 4h patch, 1h hotfix>
- **Watch end:** <UTC>
- **Mid-watch re-check:** <pass/fail + notes>
- **Incoming user reports:** <count + links to any issue(s)>
- **PyPI download count at watch end:** <number>

## Stage 7 — Closeout

- [ ] Release marked successful above
- [ ] `docs/` reflects new behavior
- [ ] `CHANGELOG.md` entry shipped as written
- [ ] README version refs updated
- [ ] Follow-up issues opened with links: <list>
- [ ] Release comms posted to: <list of channels>

Closed by: <name>
Closed at: <UTC>

---

## Release communications

Links to every communication artifact posted for this release (GitHub release, blog, social, email thread, Discussions thread). One line per artifact:

- `<channel>` — `<URL>` — `<timestamp>`

---

## Incidents / Rollback (if any)

Use `16 §7` structure.

- **Trigger time:** <UTC>
- **Detection source:** <who/what noticed>
- **Severity:** <Critical | High | Medium>
- **Decider:** <name>
- **Action:** <supersede | yank | both | wait>
- **Timeline:**
  - Trigger: <UTC>
  - Decision: <UTC>
  - Fix merged: <UTC> (<PR / SHA>)
  - Supersede tag: <UTC>
  - Supersede PyPI publish: <UTC>
  - Supersede validated: <UTC>
- **Root cause:** <one paragraph>
- **Fix:** `<SHA>`
- **Follow-up:** <issue links + target release>

If no incident, delete this section.

## Hotfix notes (if this is a hotfix)

- **Triggering release:** <vX.Y.Z>
- **Hotfix rationale:** <one paragraph per 17 §3>
- **Validation steps skipped/shortened:** <list per 17 §4.2–§4.3>
- **Follow-up items:** <list with target dates per 17 §6>

If not a hotfix, delete this section.

## Exceptions / Waivers

Any gates waived for this release.

| Gate waived | Why | Signed off by | Follow-up issue |
|---|---|---|---|
| | | | |

If none, write "None."

## Attachments / evidence

List of files attached or linked that are not already inline above (bench outputs, large logs, screenshots).

- `<artifact>` — `<URL or path>`

---

## Retrospective (optional)

For major releases or releases with incidents, a short retro:

- **What went well:** <bullets>
- **What surprised us:** <bullets>
- **What we'd change for next time:** <bullets>
```

---

## Usage rules

- **Create the file before staging starts.** The empty log is created when the release begins, populated as stages complete. Never write the log after the fact.
- **Never delete a log entry.** Supersede rather than rewrite. If a release is abandoned, the status becomes `abandoned` and the reason is appended; the file stays.
- **Cross-link.** Every incident's follow-up issues reference the release log file. Every issue that was caused by a release references the release log file in its description.
- **Store in the repo.** `docs/release-log/` is committed, so every release log is a diff on the repo history.
- **Keep forever.** Old releases may look irrelevant, but the first time a user asks "what changed in 1.3.4?" the release log is the answer.

## Why this exists

Release logs are boring to write and invaluable to read. A release with a poor log looks identical to a release with a good log — right until the first time something goes wrong three weeks later. At that point, the log is either the first thing you consult or the first thing you wish existed.

The template's job is to make the boring writing easier, so we actually do it.
