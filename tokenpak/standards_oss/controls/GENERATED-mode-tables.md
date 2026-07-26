<!-- GENERATED FILE — DO NOT EDIT BY HAND. -->
<!-- generator: tools/standards.py · source-hash: 8f4897cf463a397b -->

# Mode tables (generated)

Generated from `controls/controls.yaml` and `profiles/authority/*.yaml`.
Regenerate with `python tools/standards.py generate`. Manual edits are overwritten and
fail `check-generated` in the meantime.

## Protected controls

These read identically under every authority profile. That is the design, not an omission.

| Control | Category | Executor | Authorizer | Independence |
|---|---|---|---|---|
| `publish.external-irreversible` | human-authorized | any | operator | independent |
| `legal.commitment` | non-delegable | operator | operator | independent |
| `finance.payment-authorize` | human-authorized | any | operator | independent |
| `finance.payment-destination-new` | non-delegable | operator | operator | independent |
| `data.disclose-external` | human-authorized | any | operator | separate-actor |
| `data.destroy` | human-authorized | any | operator | separate-actor |
| `access.credential-change` | human-authorized | any | operator | separate-actor |
| `access.key-custody-transfer` | non-delegable | operator | operator | independent |
| `access.grant-escalation` | human-authorized | any | operator | separate-actor |
| `record.history-alter` | human-authorized | any | operator | independent |
| `recovery.rollback` | human-authorized | any | operator | separate-actor |

## Overridable controls by authority profile

| Control | assisted | bounded-autonomous | strict | supervised |
|---|---|---|---|---|
| `work.accept` | operator / explicit / separate-actor | reviewer / explicit / separate-actor | reviewer / explicit / independent | reviewer / explicit / separate-actor |
| `work.scope-change` | operator / explicit / none | delegate / standing-envelope / none | operator / explicit / separate-actor | operator / explicit / none |
| `integrate.shared-baseline` | operator / explicit / separate-actor | delegate / standing-envelope / separate-actor | reviewer / explicit / independent | reviewer / explicit / separate-actor |
| `publish.external-revocable` | operator / explicit / separate-actor | delegate / standing-envelope / separate-actor | operator / explicit / independent | operator / explicit / separate-actor |
| `commit.external-promise` | operator / explicit / separate-actor | operator / explicit / separate-actor | operator / explicit / independent | operator / explicit / separate-actor |
| `spend.paid-action` | operator / explicit / none | none / standing-envelope / none | none / standing-envelope / none | none / standing-envelope / none |
| `spend.limit-change` | operator / explicit / none | operator / explicit / none | operator / explicit / separate-actor | operator / explicit / none |
| `governance.waiver` | operator / explicit / separate-actor | operator / explicit / separate-actor | operator / explicit / independent | operator / explicit / separate-actor |
| `governance.standard-change` | operator / explicit / separate-actor | operator / explicit / separate-actor | operator / explicit / independent | operator / explicit / separate-actor |

Cell format: **authorizer / authorization type / independence requirement**.

Assurance level for every row: `procedural`. This corpus provides `declared` and
`validated` only — no row here is enforced at runtime (GOVERNANCE.md R23).
