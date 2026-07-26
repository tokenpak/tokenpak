---
id: BS-COMMITMENTS-MONEY-AND-CONTRACTUAL-ACTIONS
layer: commitments
risk_class: critical
default_coverage_profiles: [product-delivery, multi-agent]
control_points:
  [finance.payment-authorize, finance.payment-destination-new, legal.commitment, commit.external-promise]
---

# Money and contractual actions

## Purpose

Govern the actions that move money or create legal obligation — the two categories where a mistake is
least reversible and least forgiving.

## Applies to

Any operator whose work touches payment, pricing, refunds, invoicing, procurement, or agreements.
**Read when triggered** — and the trigger is the first time money or a signature is in scope, not the
first time something goes wrong.

---

## Requirements

### Money moves under human authority

**R1.** Authorizing a payment is protected (`finance.payment-authorize`). An agent MAY prepare,
calculate, reconcile, and present. A human authorizes.

**R2.** Establishing a **new destination** for money is non-delegable
(`finance.payment-destination-new`). A human performs it, and the destination is verified **out of
band** — through a channel other than the one that requested it.

**R3.** R2 exists because payment-redirection fraud works by compromising the requesting channel. An
instruction to change bank details, arriving through the same channel as the invoice, verified by
replying to that channel, is verified by the attacker.

**R4.** Each payment is authorized individually: this amount, this destination, this obligation.
There is **no standing authority** to pay.

**R5.** Before authorizing, verify: the amount, the destination against the established record, and
the obligation basis — what this pays for.

**R6.** Payment execution follows `BS-DELEGATION-SPEND-AND-RESOURCE-LIMITS` R11–R14: record intent
before, record outcome after, and **never auto-retry an ambiguous result**. Resolve ambiguity by
inspecting the authoritative source.

### Money values are owned

**R7.** Prices, rates, discounts, refunds, and credits trace to a decision by whoever owns pricing.
They are not derived, inferred, or calculated into existence by whoever needs a number.

**R8.** A money value shown to an outside party MUST be traceable to that decision. "The system
calculated it" is not a basis when the calculation was never authorized.

**R9.** Changing a money value is a recorded decision naming the authority and the effective date.
Retroactive changes additionally require a decision about who is affected and how they are told.

**R10.** **Agents MUST NOT issue refunds, credits, discounts, or waivers** absent an explicit,
bounded, expiring delegation stating the maximum value and the conditions. Without that instrument,
the answer is escalation, not judgement.

### Legal commitments

**R11.** Agreeing to terms or signing on the operator's behalf is **non-delegable**
(`legal.commitment`). A human performs the act.

**R12.** Before agreeing, the obligations MUST be enumerated: what you must do, by when, what happens
if you do not, how it ends, and what it says about liability and the ownership of what you produce.

**R13.** Accepting terms of service, licences, and platform agreements is a legal commitment. It is
routinely treated as a click, and it routinely creates real obligation.

**R14.** An agent MAY summarise an agreement, extract obligations, and flag concerns. It MUST NOT
accept, and where technically possible it MUST NOT be able to.

**R15.** Legal review is a **judgement about whether to be bound**, and it is separate from checking
whether a document is well-formed. An agent can do the second. Only a human decides the first.

### Obligations you have taken on

**R16.** Maintain a register of standing obligations: recurring payments, renewals, notice periods,
service commitments, and their dates. Obligations you have forgotten are still obligations.

**R17.** Auto-renewing commitments MUST have a review date **before** the notice period closes.

**R18.** Where an obligation depends on your capacity — a service level, a delivery date, a support
commitment — it is also a stakeholder commitment and is governed by
`BS-COMMITMENTS-STAKEHOLDER-AND-SUPPORT`.

---

## Evidence and acceptance

You can produce: every established payment destination and when it was verified out of band; every
money value shown externally and the decision behind it; every standing obligation and its next
review date; and every delegation instrument permitting an agent to act on value, with its bound and
expiry.

## Control points

| Control | Category | Notes |
|---|---|---|
| `finance.payment-authorize` | Protected — human-authorized | Per payment, never standing |
| `finance.payment-destination-new` | Protected — non-delegable | Out-of-band verification mandatory |
| `legal.commitment` | Protected — non-delegable | Human performs; agents prepare only |
| `commit.external-promise` | Overridable, operator by default | Capacity verified first |

No authority profile changes the first three. That is the point of them.

## Exceptions and stop conditions

**Stop** when: a destination change arrives through the same channel as the request; a payment lacks
an obligation basis; an agreement's obligations are not enumerated; an agent is asked to grant value
without a delegation instrument; or a payment result is ambiguous.

There is **no expedited path** through R2 or R11. Urgency is the standard pretext for both
payment-redirection fraud and for signing something nobody read.

## Anti-patterns

- Verifying new bank details by replying to the email that requested the change.
- Standing approval to "handle the monthly payments".
- An agent issuing a goodwill credit because the customer was upset.
- A price quoted from a calculation nobody authorized.
- Clicking through platform terms as a routine step in an install script.
- An auto-renewal noticed the day after the notice period closed.
- Retrying a payment whose result was unclear.

## Templates

`templates/decision-record.md` · `templates/waiver.md`

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
