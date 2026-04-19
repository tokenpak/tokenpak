---
title: TokenPak Post-Deploy Validation
type: standard
status: draft
depends_on: [11-release-workflow-overview.md, 14-production-deployment-runbook.md]
---

# TokenPak Post-Deploy Validation

After production publish is technically successful, this document verifies the release is actually healthy in the places users interact with it. A release is not "done" until this document's checks pass and the watch period closes.

Every check has the same shape as the staging checklist (13): owner, pass/fail, evidence. Copy this into the release log (19) for each release.

---

## 1. Release under validation

- **Version:** `vX.Y.Z`
- **PyPI URL:** `https://pypi.org/project/tokenpak/X.Y.Z/`
- **GitHub release:** `https://github.com/tokenpak/tokenpak/releases/tag/vX.Y.Z`
- **Deploy completed:** `<UTC timestamp from 14 §8>`
- **Owner:** `<release owner>`
- **Watch period ends:** `<UTC timestamp, typically deploy + 24h>`

---

## 2. Technical health checks

Run on the operator's machine within the first 30 minutes after `14 §6.3` reports `X.Y.Z` on PyPI.

| # | Check | Owner | Pass/Fail | Evidence |
|---|---|---|---|---|
| 2.1 | `pip install tokenpak==X.Y.Z` on a freshly created venv succeeds. | | | |
| 2.2 | PyPI download page shows sdist and wheel files both available. | | | |
| 2.3 | `tokenpak --version` reports `X.Y.Z`. | | | |
| 2.4 | `tokenpak doctor` exits 0 on the fresh install. | | | |
| 2.5 | `tokenpak serve` starts cleanly, binds 127.0.0.1:8766, no WARNINGs. | | | |
| 2.6 | A proxied request to the configured provider completes successfully. | | | |
| 2.7 | No bytes of the request or response are altered on passthrough paths (Constitution §5.2). | | | |

## 3. Core user-journey checks

These prove the user's first hour still works.

| # | Journey | Owner | Pass/Fail | Evidence |
|---|---|---|---|---|
| 3.1 | Quickstart (`docs/quickstart.md`) from zero reaches a savings panel within 5 min. | | | |
| 3.2 | `tokenpak integrate claude-code --apply` on a machine with Claude Code installed modifies the expected file and nothing else. | | | |
| 3.3 | `tokenpak integrate cursor` path renders a valid configuration instruction block. | | | |
| 3.4 | `tokenpak savings` reports real numbers after a few proxied requests. | | | |
| 3.5 | The dashboard loads, shows the savings hero metric, and empty states match `04 §10` where data is missing. | | | |

## 4. Metrics verification

The goal: the numbers the user and the dashboard see are the numbers the monitor DB actually has.

| # | Check | Owner | Pass/Fail | Evidence |
|---|---|---|---|---|
| 4.1 | `tokenpak savings --json` field values match the `monitor.db` rows for the same window. | | | |
| 4.2 | `cache_origin` distribution looks sane: `proxy`, `client`, and (possibly) `unknown` all present in expected proportions for the test traffic. No NULLs. | | | |
| 4.3 | `tokenpak cost` numbers match the computed sum from `monitor.db`. | | | |
| 4.4 | No metric in the dashboard reports zero where data exists (empty-state text only). | | | |
| 4.5 | No metric in the dashboard over-claims — compression savings and cache savings are separate lines. | | | |

## 5. Token savings / telemetry sanity

| # | Check | Owner | Pass/Fail | Evidence |
|---|---|---|---|---|
| 5.1 | Running `tokenpak demo` produces the same percentage savings (±5%) as the README advertises. | | | |
| 5.2 | A long agent workload (the reference benchmark, or a 100-request agent session) shows savings consistent with the previous release within ±5%. | | | |
| 5.3 | If telemetry is enabled for this operator's validation, the outbound payload is redacted (no prompts, no credentials, no PII). | | | |
| 5.4 | Telemetry opt-in is off by default on the clean install. | | | |

## 6. CLI / dashboard sanity

| # | Check | Owner | Pass/Fail | Evidence |
|---|---|---|---|---|
| 6.1 | `tokenpak --help` output matches the help text in `docs/`. | | | |
| 6.2 | Every verb listed in `03-cli-ux-standard.md §1` runs. | | | |
| 6.3 | Dashboard first paint under 500ms. | | | |
| 6.4 | Dashboard icons from one set (no mixing per `05 §9`). | | | |
| 6.5 | Dashboard colors match Brand Style Guide tokens (`05 §5`). | | | |

## 7. Error and alert review

| # | Check | Owner | Pass/Fail | Evidence |
|---|---|---|---|---|
| 7.1 | No ERROR-level log lines in the operator's `tokenpak serve` logs during validation. | | | |
| 7.2 | No stack traces in the user-visible CLI output during any validation step. | | | |
| 7.3 | Exit codes on induced failures match `03-cli-ux-standard.md §3`. | | | |
| 7.4 | If a public support channel (GitHub Issues, Discussions) exists, no new bug reports referencing `X.Y.Z` in the first watch period (see §8). Record the count; zero is expected. | | | |

## 8. Watch period

A watch period is the time between deploy and "release done." Default length is **24 hours** for minor/major, **4 hours** for patches, **1 hour** for hotfixes.

| # | Check | Owner | Pass/Fail | Evidence |
|---|---|---|---|---|
| 8.1 | At the half-watch mark, re-run the technical health checks (§2). No regression. | | | |
| 8.2 | PyPI download stats show non-zero installs (users are picking it up). | | | |
| 8.3 | No incoming user reports of breakage on GitHub Issues, Discussions, or other public channels. | | | |
| 8.4 | No private reports from known power users / channel partners. | | | |

During the watch period, the release owner is on point. They do not start another release until the watch closes.

## 9. Final release confirmation

When the watch period closes and every box above is checked:

- [ ] Release log entry (19) marked **successful**.
- [ ] `docs/` reflects the new features / behavior.
- [ ] `CHANGELOG.md` entry reads as shipped (no TODOs, no "coming soon").
- [ ] README version badge, if static, updated to `X.Y.Z`.
- [ ] Any follow-up items (items 8 §4 support threads, known issues to fix next) opened as issues with release `X.Y.Z` referenced.
- [ ] Release comms (18) posted if the release warrants an announcement.

Only after all boxes are checked is the release **closed**.

## 10. If validation fails

- **Any §2–§7 check fails:** stop validation, evaluate severity with the rollback decider (`16-rollback-and-recovery-runbook.md` §2).
- **A §8 watch-period regression surfaces:** same path — evaluate, decide rollback / patch / waiver.
- **§9 cannot be checked because a follow-up is open:** extend the watch period until the follow-up is resolved, or document the incomplete closeout in the release log with a reason.

A failed post-deploy check is itself evidence. Record it in the log with:

- What check failed.
- Observed vs expected.
- Action taken (patch, rollback, waiver, extend watch).
- Who decided.
- Timestamp.

## 11. Evidence to capture

In the release log entry (19):

- Clean-venv install output (§2.1).
- `tokenpak savings --json` output at 30 min and at watch-period end.
- `monitor.db` row counts / cache_origin distribution at watch-period end.
- PyPI download count at watch-period end.
- Screenshot or text dump of dashboard landing page.
- Any errors encountered + their resolutions.
