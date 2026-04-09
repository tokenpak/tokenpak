---
name: incident-pack
description: "incident outage service down investigate on-call runbook incident-pack incident-response — surface recent incidents, impacted touchpoints, log query templates, and runbook links for a service or error. Pass a service name or error string."
allowed-tools:
  - mcp__tokenpak-claude-code__build_context_pack
  - mcp__tokenpak-claude-code__search_corpus
  - mcp__tokenpak-claude-code__summarize_related_issues
  - mcp__tokenpak-claude-code__extract_structured_fields
user-invocable: true
disable-model-invocation: false
---

# /incident-pack

Assemble an incident response pack for a service or error. Invoke
`mcp__tokenpak-claude-code__build_context_pack` with the argument from
`$ARGUMENTS` as the `query`, with `include_related: true`.

- If `$ARGUMENTS` is non-empty, use it verbatim as the `query`
  (e.g. `"auth-service"`, `"payment"`, `"503 upstream"`, `"Cannot read properties of undefined"`).
- If `$ARGUMENTS` is empty, use `"incident"` as the `query`.

After `build_context_pack` returns, call `mcp__tokenpak-claude-code__search_corpus`
with `query: "$ARGUMENTS runbook"` and `top_k: 5` to surface runbook entries
specifically. Use both tool outputs to populate the four sections below.

Do NOT call `summarize_related_issues` or `extract_structured_fields` separately —
`build_context_pack` chains them internally.

If any tool returns `status: "no-corpus"`, display the hint message for that section
and stop — do not fabricate incident history or runbook content.

---

## Recent Incidents

Surface prior incidents matching the service or error from `related_issues` and
`compacted_context` in the `build_context_pack` output. Filter to entries whose
`source_path` or `snippet` reference an incident, outage, postmortem, or failure.
List up to 5, most relevant first (use `score` as a proxy for recency when timestamp
is unavailable).

```
Recent incidents for: <$ARGUMENTS or "incident">

• <source_path> — <snippet (truncated to 120 chars)>
  severity: <severity or "—"> | date: <date or "unknown">
...
```

If no incidents are found: "No prior incidents found in corpus for `<$ARGUMENTS>`."

---

## Impacted Code

Extract code touchpoints from `entities` using:
- `entities.api_endpoints` — API surface implicated in the incident
- `entities.config_keys` — configuration keys relevant to the failure
- `entities.decisions` — architectural decisions that bear on the incident

Combine with any `summary.risks` entries that reference specific files, paths,
or modules. List each touchpoint as a bullet with its source and a one-line note.
If none are found, write "No impacted code touchpoints found in corpus."

```
⚠️ <file, endpoint, or config key> — <note>
...
```

---

## Log Queries

Extract log query templates from corpus hits (`compacted_context` and the runbook
`search_corpus` results). Look for grep patterns, log filter expressions, structured
log queries (Datadog, Loki, CloudWatch, journald, etc.), or example command lines
that help isolate the failure. Present each as a fenced code block with a one-line
label.

If none are found in corpus, provide two generic fallback templates:

```bash
# Filter by service name in journald
journalctl -u <service> --since "1 hour ago" | grep -i "error\|fatal\|panic"

# Grep application logs for error string
grep -r "<$ARGUMENTS>" /var/log/ --include="*.log" | tail -50
```

---

## Runbooks

List runbook entries from the `search_corpus` runbook query results. Use each hit's
`source_path` as the reference and its `snippet` as the description. If a `url` or
`link` field is present in `entities`, include it as a clickable reference. List up
to 5, most relevant first.

```
Runbooks for: <$ARGUMENTS or "incident">

• <source_path> — <snippet (truncated to 120 chars)>
...
```

If no runbooks are found: "No runbooks found in corpus for `<$ARGUMENTS>`. Ensure
the vault's runbook directory is indexed: `tokenpak index <vault_root>`."

---

After all four sections, print a one-line compact summary:

```
incident-pack: <corpus_hits> hits · <N> related · <N> runbooks · service:<$ARGUMENTS or "incident">
```
