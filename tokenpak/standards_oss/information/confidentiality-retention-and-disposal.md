---
id: BS-INFORMATION-CONFIDENTIALITY-RETENTION-AND-DISPOSAL
layer: information
risk_class: high
default_coverage_profiles: [delegated-work, product-delivery, multi-agent]
control_points: [data.disclose-external, data.destroy]
---

# Confidentiality, retention, and disposal

## Purpose

Handle information that is not yours — a client's, a customer's, a partner's, a person's — with the
care its owner assumed you would apply, and stop holding it when you no longer need it.

## Applies to

Any operator who holds data originating outside their own work. **Configure now if you handle
anything belonging to anyone else.**

---

## Requirements

### Know what you hold

**R1.** Maintain a **data inventory**: what you hold, whose it is, why you have it, where it lives,
who can reach it, and when it goes. Approximate and current beats precise and stale.

**R2.** Classify data by the consequence of disclosure, not by how it was labelled when you got it:

| Class | Test |
|---|---|
| Public | Disclosure has no consequence |
| Internal | Disclosure is embarrassing but not harmful |
| Confidential | Disclosure harms a party who trusted you |
| Restricted | Disclosure harms a person — identity, health, finances, location, communications |

**R3.** Unclassified data is treated as **confidential** until classified. Defaulting downward under
uncertainty is how data leaks.

**R4.** Data received from an outside party is theirs regardless of how you received it. An unmarked
attachment is not public domain.

### Collect and hold less

**R5.** Collect only what the work requires. Data you do not hold cannot leak, cannot be subpoenaed,
and does not need protecting.

**R6.** Where a partial or redacted form suffices, use it. Identifiers, samples, and aggregates often
answer the question that a full extract was requested for.

**R7.** Copies multiply obligations. Every export, backup, local copy, and pasted excerpt is another
place the data lives and another place it must be disposed of.

**R8.** Data MUST NOT be sent to a third-party service — including a model — unless that flow is
declared, classified, and permitted. **Pasting is a disclosure.**

### Disclosure

**R9.** Disclosure outside the declared boundary is protected (`data.disclose-external`): authorized
per disclosure, naming the recipient and the dataset, with minimisation applied first.

**R10.** Purpose limitation binds. Data given to you for one purpose is not available for another
without permission — including using a client's material to build something for someone else.

**R11.** Agents MUST NOT disclose data outside the boundary on their own authority, in any authority
profile. Where an agent's task requires an outward flow, that flow is declared in its envelope.

**R12.** Disclosures MUST be recorded: what, to whom, when, on whose authority, under what basis.

### Retention and disposal

**R13.** Every data class has a **stated retention period** and a disposal path. "Indefinitely" is a
decision requiring a reason, not a default.

**R14.** Retention obligations run in both directions: some data must be kept for a period, and some
must not be kept beyond one. Check both before disposing.

**R15.** Disposal is protected (`data.destroy`): enumerate the specific data, confirm no retention
obligation applies, and confirm the restore path — or record that the loss is accepted.

**R16.** Destroy by **enumeration, never by pattern**. A pattern evaluated at execution time matches
things you did not anticipate.

**R17.** Disposal MUST cover copies: backups, exports, caches, and derived artifacts. Deleting the
primary while backups persist is not disposal; it is a false record of disposal.

**R18.** When an engagement ends, run disposal deliberately. Data retained by inertia after a
relationship ends is pure liability.

### People's data

**R19.** Where you hold data about identifiable people, they retain rights over it regardless of your
convenience — at minimum: knowing you hold it, correcting it, and having it removed where no
obligation requires keeping it.

**R20.** A request from a person about their own data MUST have a defined handling path and a
responder. An unanswered request is a decision to refuse.

---

## Evidence and acceptance

You can produce the inventory, each class's retention period, the record of outward disclosures for a
period, and evidence that a disposal actually removed copies. Test the last one — it is where the gap
usually is.

## Control points

| Control | Category |
|---|---|
| `data.disclose-external` | Protected — human-authorized, per disclosure |
| `data.destroy` | Protected — human-authorized, enumerate first |

## Exceptions and stop conditions

**Stop** when: data is unclassified and the action is consequential; a flow to a third party is not
declared; disposal cannot confirm the retention position; you cannot enumerate what would be
destroyed; or an agent's task implies an outward flow not in its envelope.

## Anti-patterns

- Pasting a client document into an external tool to summarise it.
- A "temporary" export in a working folder, two years later.
- Classifying by the sender's label rather than the consequence.
- Reusing one client's material for another's deliverable.
- Deleting the primary store while backups retain everything.
- Disposing by wildcard.
- Keeping everything indefinitely because deciding retention was harder.
- An unanswered request from a person about their own data.

## Templates

`templates/data-inventory.md` · `templates/decision-record.md`

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
