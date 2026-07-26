# Domain modules

The layers above `domains/` — core, delegation, commitments, information, continuity — are the
**spine**. They are domain-neutral: they hold whether your work produces software, research,
documents, designs, campaigns, client deliverables, or business decisions.

A **domain module** adds requirements that only make sense for a specific kind of work, and registers
the concrete actions of that domain against the spine's control classes.

One module ships with this corpus: `software-delivery/`, for work that ships installable, versioned
software. It is a module rather than part of the spine on purpose — most operators using this corpus
do not ship software, and those who do still need the spine first.

---

## 1. The rules a module must follow

**M1.** A module MAY add requirements. It MUST NOT remove, weaken, or contradict a spine requirement.

**M2.** A module registers **instances** against existing control classes in `controls/controls.yaml`.
It does not invent new protected categories.

**M3.** An instance inherits its class's authority values and MAY tighten them. It MUST NOT loosen
them.

**M4.** A module MUST NOT change what is protected. If your domain has an irreversible action, it maps
to `publish.external-irreversible` or another existing class — it does not become a new kind of
exemption.

**M5.** A module's standards use the same document shape and the same `BS-` ID convention.

**M6.** A module states its **trigger**: the observable condition that makes it apply.

---

## 2. Registering an instance

An instance is a concrete action in your domain, mapped to a control class:

```yaml
# domains/<your-domain>/instances.yaml
instances:
  - id: publishing.send-client-report
    class: publish.external-irreversible
    description: Sending a final report to a client
    rationale: >
      Once sent, the client may forward, quote, or act on it. Recall is not possible.
    tightens:
      independence_requirement: independent
```

The class determines the authority. The instance says which of your real actions it covers, and why.

Writing this file is usually where the value is: enumerating your actual irreversible actions is the
work, and mapping them is the easy part.

---

## 3. Mapping non-software work onto the spine

If you are not shipping software, you do not need a module to start. The spine covers you; these
mappings show how.

### Research and analysis

| Domain reality | Spine treatment |
|---|---|
| A finding you will publish or act on | Deliverable class **document or analysis**; claims traceable to sources |
| Sharing a dataset with a collaborator | `data.disclose-external` — protected |
| Publishing a paper, dataset, or preprint | `publish.external-irreversible` |
| Retracting or correcting | Correct forward; retraction is `record.history-alter` |
| A result that did not replicate | Truth and evidence R10 — no post-hoc selection |

### Client and professional services

| Domain reality | Spine treatment |
|---|---|
| A proposal with a date and a price | `commit.external-promise` + money standard |
| Statement of work changes | `work.scope-change`, recorded and mutually acknowledged |
| Sending a deliverable | `publish.external-irreversible` if they may forward or act on it |
| Client material in your workspace | Confidentiality standard; purpose limitation binds |
| Engagement ends | Deliberate disposal — retention by inertia is liability |

### Content, marketing, and communications

| Domain reality | Spine treatment |
|---|---|
| Anything published | `publish.external-revocable` or `-irreversible` — assume irreversible if it can be shared onward |
| A performance or outcome claim | External-claims standard; class it, substantiate it |
| A campaign commitment | `commit.external-promise` |
| Correcting a published error | Correct forward, visibly |

### Business operations

| Domain reality | Spine treatment |
|---|---|
| Vendor selection and onboarding | Decision class; record alternatives and reasoning |
| Paying an invoice | `finance.payment-authorize` — protected |
| New supplier bank details | `finance.payment-destination-new` — non-delegable, out-of-band verification |
| Signing anything | `legal.commitment` — non-delegable |
| Changing a price | Money standard R7–R9 |
| Hiring or granting system access | `access.grant-escalation` |

If a mapping is unclear, apply the classification rule from
`BS-CORE-RISK-AND-PROTECTED-ACTIONS` R17: **the more protected category applies until someone with
authority classifies it.**

---

## 4. When to write a module instead

Write one when your domain has recurring requirements the spine cannot express — a specific evidence
form, a domain-mandated procedure, a regulator's expectation, a repeated sequence with its own failure
modes.

Do not write one merely to restate the spine in your vocabulary. That produces two documents that
drift apart, and the drift is silent until it matters.

Start with `instances.yaml` alone. Most operators need only that.
