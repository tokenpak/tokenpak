---
title: TokenPak Release Communication Template
type: standard
status: draft
depends_on: [11-release-workflow-overview.md, 16-rollback-and-recovery-runbook.md, 17-hotfix-workflow.md]
---

# TokenPak Release Communication Template

Every release message, across every channel, follows one of the templates below. The goal is that a user reading two different channels for the same event sees the same facts in the same order.

Fill the angle-bracket placeholders. Do not embellish beyond them. Tone is set by `05-brand-style-guide.md §3` and Constitution §8 — direct, specific, no marketing filler.

---

## Channels

Primary channels for release communication, in priority order:

1. **GitHub release notes** — the canonical record.
2. **CHANGELOG.md** — an additive entry.
3. **tokenpak.ai** or associated blog — for announcements that warrant a longer write-up.
4. **Social** — one post per release maximum, matching the `05 §12` microcopy rules.
5. **Email / Discussions** — only for security-relevant or breaking changes.

For each release, the release owner picks the subset of channels that matter and posts the same facts on each.

---

## A. Pre-release notice

Use for major releases, breaking-change minor releases, or anything users should prepare for. Optional for patch releases.

```
Subject: TokenPak <version> — heads up

Release type: <major | minor | patch>
Target publish date: <YYYY-MM-DD>

What's coming:
- <one-line change that a user will notice>
- <another>

Heads-up items:
- <any breaking change, deprecation, or required action>
- <link to migration notes if applicable>

Pre-release candidate:
- Test PyPI: pip install -i https://test.pypi.org/simple/ tokenpak==<rc-version>
- Please try it and report issues at https://github.com/tokenpak/tokenpak/issues

Questions: <contact point>
```

---

## B. Release-start notice

Used internally when the deploy (14) begins. Posted to whatever channel the release owner uses to coordinate (may be as simple as a commit message). Not always user-visible.

```
TokenPak <version> release started at <UTC timestamp>.
Tag: vX.Y.Z (<SHA>)
Release owner: <name>
Expected window: <range>
Status: deploy in progress
```

---

## C. Release-success notice

Post within 30 min of `14 §8` success criteria being met.

```
Subject: TokenPak <version> is live

TokenPak <version> is now on PyPI.

Install or upgrade:
  pip install --upgrade tokenpak
  # or pin:
  pip install tokenpak==X.Y.Z

What's new:
- <one-line benefit>
- <another>

Highlights:
<optional one-paragraph description, only if the release warrants it>

Full notes: https://github.com/tokenpak/tokenpak/releases/tag/vX.Y.Z
CHANGELOG: https://github.com/tokenpak/tokenpak/blob/main/CHANGELOG.md
Docs: https://github.com/tokenpak/tokenpak/tree/main/docs

Thanks to <@handle>, <@handle> for contributions in this release.
```

Rules:

- First word after the subject line is what the reader should do next (install, upgrade, try).
- Do not use exclamation marks.
- Do not open with "We're excited to announce…" (Constitution §8).

---

## D. Rollback notice

Use when a production release is being rolled back or superseded per `16-rollback-and-recovery-runbook.md`.

**Post within 30 minutes of the rollback decision**, not the trigger.

```
Subject: TokenPak <broken-version> — issue identified, fix inbound

Status: <broken-version> is being superseded.
Affected users: <who hits this — "everyone who installed <broken-version>", or "users on <client>", etc.>
Impact: <one sentence, concrete — what the user sees that is wrong>

Action for users:
  # Preferred: upgrade to the fix once live (see below)
  pip install --upgrade tokenpak

  # Or pin to the prior good version until the fix lands:
  pip install tokenpak==<previous-good-version>

Root cause: <one sentence — do not speculate>
Fix ETA: <timestamp or "within the hour">

Follow-up posts:
- When the supersede ships: release-success notice for <supersede-version>.
- When validation completes: closing note.

We'll update this thread until the fix is validated.
```

If the broken release was yanked, say so explicitly — do not obscure it. "We yanked <version> because <reason>."

---

## E. Issue escalation notice

Used when a release is in production and a High-severity issue has been reported but rollback is not yet triggered (see `16 §1` Medium-to-High continuum).

```
Subject: TokenPak <version> — known issue <short>

Status: known issue with <version>; not yet superseded.
Severity: <High | Medium>
What breaks: <one sentence>
Who's affected: <one sentence>
Workaround: <exact command or steps, or "none available yet">

We're tracking at: https://github.com/tokenpak/tokenpak/issues/<N>
Decision on supersede: <timestamp by which we'll decide>
```

---

## F. Post-release summary

Optional. Used for major releases, end-of-quarter retrospectives, or when the release's story is newsworthy.

```
Subject: TokenPak <version> in review

Released: <date>
Scope: <one paragraph>

Highlights:
- <user-visible improvement with a number>
- <another>

What we learned:
- <honest one-line>
- <another>

What's next:
- <one-line about the next planned release, if announced>

Thanks to everyone who contributed: <@handles>.
```

This template is not a marketing piece. If the release was uneventful, no post-release summary is needed.

---

## Tone rules that apply to every template

- One idea per sentence.
- Numbers before adjectives.
- Active voice.
- No filler ("simply," "just," "easily," "revolutionary," "game-changing," etc. — see `10-release-quality-bar.md B3`).
- Mention specific versions, not vague references.
- Link to the canonical artifact (GitHub release) in every user-facing communication.

## What never goes in release communications

- **Tribal knowledge.** If a reader needs context from an internal thread to understand the message, the message is wrong.
- **Non-public infrastructure details.** No internal paths, agent names, or operational systems that aren't part of the shipped product.
- **Speculation about root causes.** If we don't know, say "under investigation" and update when known.
- **Apologies without fix plans.** "Sorry for the disruption" without "here's what we're doing" is empty.

## Editing and approval

- Release owner drafts.
- Reviewer (from `11 §4`) reads before posting.
- For rollback and escalation notices, publication speed is more important than editing polish. Ship the message with facts; edit the doc copy later.
- Once posted publicly, do not silently edit substantive content. Post a correction as a reply; leave the original intact for the record.
