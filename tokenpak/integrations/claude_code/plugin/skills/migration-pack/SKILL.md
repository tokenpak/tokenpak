---
name: migration-pack
description: "migration migrate rollout schema migration framework migration migration plan migration pack — build a migration pack for a target area: architecture notes, prior migrations, risky files, test plan, rollback procedures. Pass a target area."
allowed-tools:
  - mcp__tokenpak-claude-code__build_context_pack
  - mcp__tokenpak-claude-code__summarize_related_issues
  - mcp__tokenpak-claude-code__extract_structured_fields
user-invocable: true
disable-model-invocation: false
---

# /migration-pack

Build a structured migration pack for a target area. Invoke
`mcp__tokenpak-claude-code__build_context_pack` with the argument from
`$ARGUMENTS` as the `query`, with `include_related: true`.

- If `$ARGUMENTS` is non-empty, use it verbatim as the `query`
  (e.g. `"auth"`, `"database schema"`, `"payment service"`).
- If `$ARGUMENTS` is empty, use `"migration"` as the `query`.

Do NOT call `summarize_related_issues` or `extract_structured_fields`
separately — `build_context_pack` is the composite entry point and
internally chains all three tools.

If the tool returns `status: "no-corpus"`, display the hint message and
stop — do not fabricate architecture notes or migration history.

Once the tool returns, format the output into the five sections below.

---

## Architecture

Summarize the architecture context for the target area using
`summary.key_facts` and `compacted_context` from the tool output.
List the top corpus hits (up to 5) as bullet points with `source_path`
and a one-line `snippet` (truncated to 120 chars).

```
Target : <$ARGUMENTS or "migration">

Key facts:
• <fact 1>
• <fact 2>
...

Top corpus hits:
• <source_path> — <snippet>
...
```

---

## Prior Migrations

Summarize `related_issues` from the tool output, filtered to entries
whose `source_path` or `snippet` reference a previous migration, schema
change, rollout, or upgrade. List each hit with `source_path`, score,
and extracted `symbols`. If no prior migrations are found, write
"No prior migrations found in corpus."

```
Prior migrations (<N>):
• <source_path> (score: <score>) — <snippet>
  symbols: <symbols or "—">
...
```

---

## Risky Files

Extract risky files from `entities` using:
- `entities.decisions` — policy or architectural decisions that constrain the migration
- `entities.config_keys` — configuration keys that must change during migration
- `entities.api_endpoints` — API surface affected by the migration

Combine with any `summary.risks` entries that reference specific files,
paths, or modules. List each risk as a bullet with the source and a
one-line description. If none are found, write "No risky files identified
in corpus."

```
⚠️ <file or config key> — <risk description>
⚠️ <file or config key> — <risk description>
...
```

---

## Test Plan

Generate a concrete test plan drawn from `summary.constraints`,
`summary.next_actions`, and `entities.deadlines`. Each item must be a
checkbox line. Prefix deadline-driven items with 📅 and risk-driven
items with ⚠️. If nothing is found, write one generic item:
`- [ ] Run full test suite before and after migration`.

```
- [ ] <test action>
- [ ] ⚠️ <risk-driven test>
- [ ] 📅 <deadline-driven test>
...
```

---

## Rollback

Extract rollback procedures from `entities.decisions` and
`summary.constraints`. Look for any rollback, revert, or undo guidance
in corpus hits (`compacted_context`). List each procedure as a numbered
step. If no rollback procedures are found in corpus, write a generic
three-step fallback:

```
1. Revert the migration commit (git revert or equivalent).
2. Restore the previous configuration from backup.
3. Re-run smoke tests to verify the reverted state is stable.
```

---

After all five sections, print a one-line compact summary:

```
migration-pack: <corpus_hits> hits · <N> related · target:<$ARGUMENTS or "migration">
```
