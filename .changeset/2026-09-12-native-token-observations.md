---
"tokenpak": minor
---

Add opt-in authenticated native token observations with independent coverage,
bounded request reservations, and explicit response-completion evidence.
These observations do not establish billed cost, invoice reconciliation, or
accepted task outcomes. Existing priced accounting and default forwarding
remain unchanged.

The API snapshot adds 20 public bindings, including imported helper bindings,
without removing existing symbols. Additive SQLite migrations preserve prior
request rows and reservation domains. Existing requests default to incomplete
token evidence. Existing reservation and coverage rows retain NULL accounting
basis metadata; legacy domains remain priced accounting and cannot be relabeled
as native token usage. No historical row is reclassified as measured native
token usage.

The schema upgrade is one-way. For a software rollback, retain the previous
environment and the pre-upgrade database backup; do not attempt to infer or
delete newly recorded evidence from a downgraded schema.
