---
title: "TokenPak <version> — <one-line summary>"
type: release-notes
version: <version>
released: <YYYY-MM-DD>
channel: <stable | pre-release>
---

<!--
Every release, every time. Release notes are user-facing; they follow the Docs Style Guide tone rules (06 §3). Numeric claims must cite the scenario that produced them or be labeled workload-dependent.

Used for CHANGELOG.md entries and GitHub release bodies. The CHANGELOG is cumulative (newest on top); the GitHub release body is this file verbatim.

Delete this comment before committing.
-->

# TokenPak <version> — <one-line summary>

Released <YYYY-MM-DD>.

<!-- One short paragraph of framing. What's the story of this release? Answer in one or two sentences before the lists. -->

<One-paragraph summary of this release. Who benefits, and how.>

## What's new

<!-- User-visible additions. Omit the section if empty. -->

- **<Feature name>** — <one-line description with a concrete benefit>. [<Doc link>](<link>)
- **<Feature name>** — <one-line description>.

## What's improved

<!-- Non-additive improvements: better defaults, refactors with user-visible impact, performance gains. Omit if empty. -->

- **<Area>:** <what changed and what the user will notice>.
- **<Area>:** <what changed>.

## What's fixed

<!-- Bugs fixed in this release. Include the symptom the user would have hit, not the internal cause. -->

- **<Symptom>** — fixed. (<optional: issue reference>)
- **<Symptom>** — fixed.

## Breaking changes

<!-- If empty, delete this whole section. If present, this is the most important section and goes above What's New. Moving it up is OK. -->

- **<What broke>** — <what callers need to do>. See the [migration note](#migration).
- **<What broke>** — <what callers need to do>.

## Deprecations

<!-- Announced but still working. Delete section if empty. -->

- **<Deprecated thing>** — use `<replacement>` instead. Will be removed in `<version>`.

## Known issues

<!-- Issues we know about and decided to ship with. Delete section if empty. Each entry names a workaround or a target fix version. -->

- **<Issue>** — <workaround>. Tracked as <link>.

## Performance

<!-- Delete section if no measurable change. If included, cite the bench scenario and the before/after numbers. -->

- **<Hot-path scenario>:** <before> → <after> (<percent> <faster/slower>). Bench: `make bench --scenario <name>`.

## Upgrade

```bash
pip install --upgrade tokenpak==<version>
```

<!-- Include any pre- or post-upgrade commands the user needs to run. If none, say "No additional steps." -->

<Additional commands or "No additional steps.">

## Migration

<!-- Only if Breaking Changes is non-empty. Walk the user through the smallest change that gets them running again. -->

If you used <old behavior>:

```bash
<before>
```

Do this instead:

```bash
<after>
```

## Rollback

```bash
pip install tokenpak==<previous-version>
```

<!-- Include any state cleanup needed to downgrade. A migration on the way up often needs a reverse migration on the way down. -->

<Rollback-specific commands or "No additional steps.">

## Thanks

<!-- Contributors (internal + external). Optional but encouraged. -->

Thanks to <@handle>, <@handle> for contributions to this release.

---

## Release quality gate

<!-- Internal audit trail. Delete this section before publishing the GitHub release body if desired, but keep it in CHANGELOG.md for auditability. -->

- [x] Technical gates A1–A7
- [x] Consistency gates B1–B4
- [x] Documentation gates C1–C7
- [x] Messaging gates D1–D3
- [x] Operational gates E1–E6

Any waivers? <list each with rationale, or "None.">

<!--
Release-notes audit:
- Does every "What's new" bullet have a concrete user-visible benefit?
- Are the numbers in "Performance" from an actual bench run?
- If there are breaking changes, is there a migration section?
- Is the rollback path tested?
-->
