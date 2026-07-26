# Data inventory

> Template. Replace everything in angle brackets. Delete this line.
> Referenced by: `BS-INFORMATION-CONFIDENTIALITY-RETENTION-AND-DISPOSAL` R1.
> **Approximate and current beats precise and stale.** An inventory updated yearly describes last
> year's exposure.

| Field | Value |
|---|---|
| **Owner** | `<person>` |
| **Last reviewed** | `<YYYY-MM-DD>` |
| **Review cadence** | `<how often>` |

## Holdings

| # | What | Whose | Class | Why held | Where | Who can reach it | Retention | Disposal |
|---|---|---|---|---|---|---|---|---|
| 1 | `<dataset>` | `<owner party>` | `<public \| internal \| confidential \| restricted>` | `<purpose>` | `<location>` | `<actors>` | `<period>` | `<method>` |

**Unclassified data is treated as confidential until classified** (R3). If the class column is empty,
that row is confidential today.

## Copies

> Every copy is another place the data lives and another place it must be disposed of. Backups,
> exports, caches, local working copies, and pasted excerpts all count.

| Primary row | Copy location | Created by | Disposed with primary? |
|---|---|---|---|

## Outward flows

| # | Data | Recipient | Basis | Authorized by | Recurring or one-off |
|---|---|---|---|---|---|
| 1 | `<what>` | `<third party, including model or tool providers>` | `<contract, consent, obligation>` | `<who>` | |

**Sending data to a service you use is a disclosure**, not an exemption from disclosure rules.
Pasting counts.

## Retention obligations in both directions

| Data | Must keep until | Must not keep beyond | Source of obligation |
|---|---|---|---|

## People's data

| Data about people | Rights path | Responder | Response window |
|---|---|---|---|

<An unanswered request from a person about their own data is a decision to refuse. Name who answers.>

## Disposal log

| Date | What | Enumerated? | Copies covered | Authorized by |
|---|---|---|---|---|

<Destroy by enumeration, never by pattern (R16). Disposal that misses backups is a false record of
disposal (R17).>
