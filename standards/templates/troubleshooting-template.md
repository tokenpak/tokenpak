---
title: <Short symptom description>
rung: 4
audience: A TokenPak user who just hit this problem and wants to fix it in under a minute.
updated: <YYYY-MM-DD>
status: draft
---

<!--
One troubleshooting page = one symptom = one fix. Do not pack multiple problems into one page.

File naming: kebab-case symptom. `port-in-use.md`, `zero-savings.md`, `creds-refresh-failed.md`.

The point of a troubleshooting page is that the user finds it via the exact error text. Include the error string verbatim — that's how grep and Google find it.

Delete this comment before committing.
-->

# <Short symptom description>

## Symptom

<!-- The exact thing the user sees. Copy-paste the error message. Show output blocks. Users find this page by grepping or Googling what they saw. -->

```text
<exact error text or log line>
```

Or in the dashboard:

<!-- Delete this paragraph if this symptom doesn't surface in the dashboard. -->

> "<exact status-strip text or error banner>"

## Cause

<!-- One short paragraph. Don't lecture. The reader is blocked. -->

## Fix

<!-- The minimum sequence of commands or clicks to resolve. Numbered. Every command is runnable as-is. -->

1. <First action>

   ```bash
   <command>
   ```

2. <Second action — explain only if non-obvious.>

   ```bash
   <command>
   ```

3. <Verification.>

   ```bash
   <command>
   ```

   You should see:

   ```text
   <expected output>
   ```

## If that didn't work

<!-- One or two alternate paths. Keep short. If there are more than two, the symptom is actually multiple symptoms — split this page. -->

- **<Alternate path>** — <one sentence + link or command.>
- **<Alternate path>** — <one sentence + link or command.>

## Why this happens

<!-- Optional. Include only if understanding the cause helps the reader avoid it next time. Skip if the fix is all they need. One paragraph max. -->

## Related

<!-- Links to adjacent troubleshooting pages or the relevant guide. -->

- [<Adjacent symptom>](<relative-link>.md)
- [<Relevant guide>](../guides/<guide>.md)

<!--
Troubleshooting audit:
- Is the exact error text in the Symptom section so grep/search finds it?
- Is the Fix a sequence of runnable commands, in order?
- Does the Verification step prove it worked?
- Is this page about exactly one symptom?
-->
