---
name: release-pack
description: "release ship rollout changelog release-notes release-plan release-pack version-bump — merged work, changelog, rollout risks, and validation checklist for a release. Pass a version tag or branch (e.g. v1.2.0, HEAD)."
allowed-tools:
  - mcp__tokenpak-claude-code__prepare_review_packet
  - mcp__tokenpak-claude-code__build_context_pack
  - mcp__tokenpak-claude-code__search_corpus
  - mcp__tokenpak-claude-code__extract_structured_fields
  - mcp__tokenpak-claude-code__summarize_related_issues
user-invocable: true
disable-model-invocation: false
---

# /release-pack

Assemble a release bundle for a version tag or branch.

- If `$ARGUMENTS` is non-empty, use it as the branch/ref (e.g. `"v1.2.0"`, `"HEAD"`, `"release/2.0"`).
- If `$ARGUMENTS` is empty, use `"HEAD"`.

**Step 1 — Merged work:** Call `mcp__tokenpak-claude-code__prepare_review_packet` with
`branch: "$ARGUMENTS"` (or `"HEAD"` if empty). This chains all five atomic tools internally
and returns `{diff_summary, corpus_hits, entities, related_issues, compacted_context, summary}`.

**Step 2 — Changelog entries:** Call `mcp__tokenpak-claude-code__build_context_pack` with
`query: "release changelog $ARGUMENTS"` and `include_related: true`.
Use `compacted_context` and `summary.key_facts` to surface changelog items from the vault.

**Step 3 — Rollout risks:** Call `mcp__tokenpak-claude-code__search_corpus` with
`query: "$ARGUMENTS rollout risks breaking changes migration"` and `top_k: 5`.
Supplement with `summary.risks` from both Step 1 and Step 2 outputs.

**Step 4 — Structured fields:** Call `mcp__tokenpak-claude-code__extract_structured_fields`
with `text: <compacted_context from Step 2>` and
`types: ["version", "api_endpoint", "config_key", "breaking_change", "deadline"]`.
Use the result to populate the Validation Checklist.

**Step 5 — Related issues:** Call `mcp__tokenpak-claude-code__summarize_related_issues`
with `query: "$ARGUMENTS release"` and `top_k: 5`.
Use these to augment the Validation Checklist with known open issues or blockers.

If any tool returns `status: "no-corpus"`, display its `hint` for that section and continue
with the remaining tools. Do not fabricate release history, changelog entries, or risk data.

---

## Merged Work

List commits or changes merged since the previous release, drawn from
`prepare_review_packet`'s `diff_summary` and `related_issues` fields.
Filter `related_issues` to entries whose `snippet` or `source_path` references a merge,
commit, PR, or feature. List up to 10, most impactful first.

```
Merged since <$ARGUMENTS or "HEAD">:

• <source_path or commit ref> — <snippet (truncated to 120 chars)>
  author: <person if available> | date: <date or "unknown">
...
```

If no merged work is found: "No merged work found in corpus for `<$ARGUMENTS>`."

---

## Changelog

Surface changelog entries from `build_context_pack`'s `compacted_context` and
`summary.key_facts`. Look for entries that reference features added, bugs fixed,
deprecations, or API changes. Present in descending relevance order.

```
Changelog for <$ARGUMENTS or "HEAD">:

• [FEATURE] <description> — <source_path>
• [FIX]     <description> — <source_path>
• [BREAKING] <description> — <source_path>
• [DEPREC]  <description> — <source_path>
...
```

If no changelog entries are found: "No changelog entries found in corpus for `<$ARGUMENTS>`.
Ensure release notes are indexed: `tokenpak index <vault_root>`."

---

## Rollout Risks

Combine `search_corpus` hits (Step 3) with `summary.risks` from both Step 1 and Step 2.
Flag items with severity where derivable (`high` / `medium` / `low`).
Highlight breaking changes and migration requirements.

```
Rollout risks for <$ARGUMENTS or "HEAD">:

⚠️ <risk description> — <source_path>
   severity: <high|medium|low|unknown> | type: <breaking|migration|dependency|config|unknown>
...
```

If no risks found: "No rollout risks found in corpus for `<$ARGUMENTS>`."

---

## Validation Checklist

Synthesize from `extract_structured_fields` (Step 4) and `summarize_related_issues`
(Step 5). Generate a checklist covering:
- API endpoints changed (from `entities.api_endpoints` and `extract_structured_fields`)
- Config keys requiring update (from `entities.config_keys`)
- Known open issues or blockers (from `summarize_related_issues`)
- Deployment steps implied by `summary.next_actions`
- Any deadlines from `entities.deadlines`

```
Validation checklist for <$ARGUMENTS or "HEAD">:

- [ ] Smoke-test changed API endpoints: <list or "none found">
- [ ] Update config keys: <list or "none found">
- [ ] Resolve open blockers: <issues from summarize_related_issues or "none found">
- [ ] Run migration scripts (if any breaking changes above)
- [ ] Verify no regressions in: <key_facts highlights>
- [ ] Tag and push release: git tag <$ARGUMENTS> && git push --tags
```

---

After all four sections, print a one-line compact summary:

```
release-pack: <corpus_hits> hits · <N> risks · <N> checklist items · ref:<$ARGUMENTS or "HEAD">
```
