---
name: review-pack
description: "review PR review pull request review code review review packet review context bundle diff review — build a structured review bundle: diff context, related tickets, code standards, and review checklist. Pass a PR, diff ref, or file."
allowed-tools:
  - mcp__tokenpak-claude-code__prepare_review_packet
  - mcp__tokenpak-claude-code__build_context_pack
  - mcp__tokenpak-claude-code__summarize_related_issues
  - mcp__tokenpak-claude-code__search_corpus
  - mcp__tokenpak-claude-code__extract_structured_fields
user-invocable: true
disable-model-invocation: false
---

# /review-pack

Build a structured review bundle for a PR, diff ref, or file. Invoke
`mcp__tokenpak-claude-code__prepare_review_packet` with the argument from
`$ARGUMENTS` mapped as follows:

- If `$ARGUMENTS` looks like a git ref (e.g. `HEAD~1`, a branch name, or a
  commit hash), pass it as `branch`.
- If `$ARGUMENTS` is a file path, pass it as `file`.
- If `$ARGUMENTS` is inline diff text (starts with `---` / `+++`), pass it
  as `diff`.
- If `$ARGUMENTS` is empty, default to `branch: "HEAD~1"`.

Do NOT call any other tools first — `prepare_review_packet` is the composite
entry point and internally calls all five atomic tools.

Once the tool returns, format the output into the four sections below.
If the tool returns `status: "no-corpus"`, display the hint and stop — do
not fabricate content.

---

## Context

Summarize `summary.key_facts` and `compacted_context` from the tool output.
Include the diff summary (`diff_summary`) and the file under review (`file`)
if present. List the top corpus hits (up to 5) as bullet points with their
`source_path` and a one-line `snippet`.

```
Diff  : <diff_summary>
File  : <file or "—">
Branch: <branch or "—">

Key facts:
• <fact 1>
• <fact 2>
...

Top corpus hits:
• <source_path> — <snippet (truncated to 120 chars)>
...
```

---

## Related

Summarize `related_issues` from the tool output. List each hit with its
`source_path`, score, and any `symbols` extracted. If `related_issues` is
empty or 0, write "No related issues found in corpus."

```
Related issues (<N>):
• <source_path> (score: <score>) — <snippet>
  symbols: <symbols or "—">
...
```

---

## Standards

Extract applicable standards and constraints from `entities`. Use:
- `entities.decisions` — architectural or policy decisions relevant to this diff
- `entities.config_keys` — configuration keys touched or referenced
- `entities.api_endpoints` — API surface touched
- `entities.glossary` — domain terms to be aware of

If an entity list is empty, omit that sub-section. If all are empty, write
"No standards or constraints found in corpus."

```
Decisions  : <list or "—">
Config keys: <list or "—">
API surface: <list or "—">
Glossary   : <list or "—">
```

---

## Checklist

Generate a concrete review checklist drawn from `summary.risks`,
`summary.constraints`, `summary.next_actions`, and `entities.deadlines`.
Each item must be a checkbox line. Prefix risk items with ⚠️ and deadline
items with 📅. If nothing is found, write one generic checklist item:
`- [ ] Verify diff is complete and tests pass`.

```
- [ ] <action item>
- [ ] ⚠️ <risk>
- [ ] 📅 <deadline>
...
```

---

After all four sections, print a one-line compact summary:

```
review-pack: <corpus_hits> hits · <N> related · policy:<policy.mode> · <diff_summary>
```
