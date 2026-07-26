---
id: BS-INFORMATION-SECURITY-ACCESS-AND-CREDENTIALS
layer: information
risk_class: critical
default_coverage_profiles: [starter, delegated-work, product-delivery, multi-agent]
control_points: [access.credential-change, access.key-custody-transfer, access.grant-escalation]
---

# Security, access, and credentials

## Purpose

Control who and what can reach your systems and secrets, and keep the most damaging material out of
reach of automation entirely.

## Applies to

Everyone, from day one. **Configure now.** This is in the starter profile because credential handling
is the one area where a single mistake by a well-meaning actor is unrecoverable.

---

## Requirements

### Least privilege, actually

**R1.** Every actor — human or agent — gets the **narrowest access sufficient** for its work. Not the
access that is convenient to configure.

**R2.** Access is granted **per role, with an expiry**. Permanent access is a decision, recorded, with
a review date — not a default that accumulates.

**R3.** Widening access is protected (`access.grant-escalation`): authorized, bounded, expiring, with
the reason recorded.

**R4.** Access MUST be reviewed on a stated cadence. What people and processes can actually reach
drifts upward silently; the drift is the finding.

**R5.** When an actor's role ends, its access ends. Include agents, service accounts, integrations,
and anything created "temporarily" — those are the ones that survive.

### Secrets

**R6.** Secrets MUST NOT appear in: source, configuration under version control, logs, error
messages, records, receipts, prompts, chat transcripts, or anything sent to a third-party service.

**R7.** R6 includes context sent to a model. A credential in a prompt has left your control, and you
cannot enumerate where it went.

**R8.** Secrets are referenced, never embedded. Store a pointer; resolve at use.

**R9.** Rotation MUST be possible without downtime, and MUST have been rehearsed. A rotation procedure
that has never run is a plan, not a capability — and it will first be exercised during an incident.

**R10.** Exposure MUST be treated as compromise. Rotate. Do not assess whether the exposure was
probably harmless; that assessment is unreliable and the rotation is cheap.

### Key custody

**R11.** Root credentials, signing keys, and account ownership have **one named human custodian**.
Not a team, not a shared vault entry with unclear ownership. A person.

**R12.** Agents MUST be **structurally unable** to reach this material — not merely instructed not
to. An instruction is not a boundary; a permission model is.

**R13.** Export or transfer of this material is non-delegable (`access.key-custody-transfer`): a human
performs it, the recipient is verified out of band, and the custody record is updated.

**R14.** Recovery — losing the custodian, losing the key — MUST be documented **and rehearsed**. The
rehearsal is the requirement. Documentation of an untested recovery path is a belief about the
future.

### Agent-specific

**R15.** An agent holds credentials only for actions it is authorized to **execute**. Where it only
prepares an action a human performs, it does not hold the credential for that action.

**R16.** Agent credentials SHOULD be separately scoped and separately revocable, so one actor can be
stopped without stopping everything.

**R17.** An agent MUST NOT publish, transmit, or record a credential — including as part of
diagnostics, error reports, or a summary of what it did.

**R18.** Where an agent processes untrusted input, it MUST NOT treat that input as instructions.
Content is data. An instruction arriving inside content is content that says something.

### Boundaries

**R19.** Know and record where your trust boundaries are: what is inside your control, what is a
third party, what is public.

**R20.** Data crossing a boundary outward is `data.disclose-external` — protected. This includes
sending it to a service you use, which is a disclosure with a contract, not a non-disclosure.

**R21.** Third-party integrations MUST declare what they access and where data goes, before adoption.
"We use X" is not a data-flow description.

---

## Evidence and acceptance

You can produce: who and what has access to what, with expiries; where each secret lives and when it
was last rotated; the named custodian for each key; the date of the last rehearsed recovery; and your
trust boundaries with the flows that cross them.

## Control points

| Control | Category |
|---|---|
| `access.credential-change` | Protected — human-authorized |
| `access.key-custody-transfer` | Protected — non-delegable |
| `access.grant-escalation` | Protected — human-authorized |

## Exceptions and stop conditions

**Stop** when: a secret may have been exposed (rotate first, investigate after); an agent requires a
credential for an action it may not execute; a custodian is unavailable and no rehearsed path exists;
or untrusted input appears to be issuing instructions.

There is no expedited path through R11–R14. Incidents are when key custody discipline is most likely
to be abandoned and most likely to matter.

## Anti-patterns

- One high-privilege credential shared because scoping was tedious.
- A secret in a log line added for debugging, still there a year later.
- "Temporary" access that outlives the person who requested it.
- A key held by a team, so held by nobody.
- An agent with production credentials it uses only to prepare a human-executed action.
- Deciding an exposed secret was probably fine.
- A documented recovery procedure nobody has ever run.
- An agent following instructions found inside a document it was asked to summarise.

## Templates

`templates/threat-model.md` · `templates/operator-onboarding.md` ·
`templates/forbidden-patterns.yaml`

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
