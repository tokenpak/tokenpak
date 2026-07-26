# Release log — <version>

> Template. Replace everything in angle brackets. Delete this line.
> Referenced by: `BS-SWDEL-RELEASE-PIPELINE-AND-LOG` R10.
> **Create this file before the release starts.** Append only — never edit an earlier entry.

| Field | Value |
|---|---|
| **Version** | `<version>` |
| **Opened** | `<timestamp>` |
| **Release owner** | `<person>` |
| **Artifact identity** | `<hash, digest, or commit — filled at stage 3, not before>` |

## Scope

| In | Reference |
|---|---|
| `<change>` | `<task or change ID>` |

**Frozen at:** `<timestamp>` · **Changes after freeze:** `<none, or decision reference>`

## Stage log

| # | Stage | Owner | Timestamp | Result | Evidence |
|---|---|---|---|---|---|
| 1 | Scope frozen | | | | |
| 2 | Verified | | | | |
| 3 | Staged | | | | |
| 4 | Go / no-go | | | | |
| 5 | Published | | | | |
| 6 | Confirmed externally | | | | |
| 7 | Announced | | | | |
| 8 | Closed out | | | | |

<Record each when it happens. A stage is not complete because the next one started.>

## Gates

| Gate | Blocking or advisory | Ran? | Result | Receipt |
|---|---|---|---|---|
| `<gate>` | `<blocking \| advisory>` | `<yes/no>` | | `<where the evidence is>` |

<A gate that did not report has not passed.>

## Go / no-go decision

| Field | Value |
|---|---|
| **Decided by** | `<named authority>` |
| **Timestamp** | |
| **Decision** | `<go \| no-go>` |

**What is not verified:** <explicitly — "nothing" requires that you checked>
**Open waivers, and whether they expire before this release:** <list>
**If this is wrong, how we find out:** <detection>
**Who is available afterwards:** <who, for how long>
**Objections raised:** <who, what, how resolved>

## What did not happen

<Skipped checks, deferred evidence, things noticed and not fixed. This section is the point of the
log — a log with only successes is a summary.>

| Item | Why | Repayment owner | Due |
|---|---|---|---|

## External confirmation

<Installed or fetched as a user would, from where they would get it. Build-system success is not
confirmation.>

| Check | Result | By | When |
|---|---|---|---|

## Observation period

**Length:** `<duration>` · **Watcher:** `<person>` · **Act-on threshold:** `<what triggers action>`

## Retrospective

**Went well:** <one line>
**Did not:** <one line>
**Changing next time:** <one line>
