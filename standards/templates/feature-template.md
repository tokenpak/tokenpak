---
title: "<Feature name>"
type: feature-spec
status: draft
owner: <your name>
created: <YYYY-MM-DD>
target_release: <version or "unscheduled">
---

<!--
Delete this HTML comment before committing.

Use this template for any feature meaningfully larger than a bug fix. Keep it short — this is a spec, not a novel. One to three pages is right.

Lives in docs/features/<slug>.md when active; moves to docs/features/shipped/<slug>.md after release.
-->

# <Feature name>

## Problem

<!--
Who is blocked, frustrated, or losing money right now? One paragraph.
Name the user type. Be concrete about the cost. If the problem is hypothetical or speculative, stop here and come back when it isn't.
-->

## Proposal

<!--
What we're going to do, in plain language. Two or three sentences. The reader should be able to repeat it back.
-->

## User-visible shape

<!--
What does the user see, type, or run? Include the CLI command, the dashboard change, the new config key, or the new API surface — whichever applies. Real text, not a sketch.

Example:

  $ tokenpak recipes add ./my-recipe.yaml
  ✓ Registered: "my-recipe" (stages: dedup, alias)
  Run `tokenpak recipes list` to see all registered recipes.
-->

## Out of scope

<!--
What this feature explicitly does NOT do. List the three closest adjacent things a reader might assume we're also doing and confirm we're not. This section is often the most valuable.
-->

## Constitution + Standards impact

<!--
- Which standards does this feature touch? (If it touches none, it's probably too trivial to need this template.)
- Does it require a Constitution amendment? (If yes, separate PR first.)
- New glossary terms? List them here; add them to 08-naming-glossary.md in the implementation PR.
-->

## Design

### Architecture placement

<!--
Which subsystem owns this? Does it fit into an existing one, or does it need a new one (01-architecture-standard.md §8)?
-->

### Key decisions

<!--
The non-obvious choices. For each, a one-liner on the alternatives considered and why this one won.
-->

### Data / state

<!--
Any new tables, files, config keys, or environment variables. Point at the exact storage path.
-->

### Dependencies

<!--
New runtime dependencies? New optional dependencies? New external services? None is a valid answer.
-->

## Compatibility

<!--
- Does this break any existing API, CLI flag, or on-disk format?
- If yes: migration story?
- If no: say so explicitly.
-->

## Risks

<!--
Top two or three things that could go wrong in production. For each, one sentence on what we'll do about it.
-->

## Telemetry

<!--
How will we know this feature is actually being used and working?
Point at specific monitor.db columns, log keywords, or dashboard signals.
Per Constitution §5.3, attribution must be honest — define cache_origin and savings attribution up front.
-->

## Test plan

<!--
- Unit tests: which subsystems?
- Integration tests: which scenarios?
- Manual verification: what will a human click or type?
- Performance: does this touch the hot path? If yes, which bench scenario protects it?
-->

## Docs plan

<!--
Which doc rungs change?
- README updated? (only if user-visible)
- Quickstart updated? (only if part of first-run flow)
- New guide? (quot when?)
- API reference?
- Troubleshooting entry? (for any new error class this introduces)
-->

## Rollout

<!--
- Feature flag or shipped on by default?
- What version does this land in?
- Anything we want to announce / blog about?
-->

## Open questions

<!--
List them. A good feature spec ships with open questions — they're the reader's review hook.
-->
