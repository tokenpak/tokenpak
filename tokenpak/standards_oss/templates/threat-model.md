# Threat model — <system or workflow>

> Template. Replace everything in angle brackets. Delete this line.
> The **shape** is the value here: assets → boundaries → adversaries → controls → non-goals. A short
> honest model beats a long speculative one.

| Field | Value |
|---|---|
| **Scope** | `<what this covers>` |
| **Out of scope** | `<what it does not — stated, so the gap is deliberate>` |
| **Owner** | `<person>` |
| **Date** | `<YYYY-MM-DD>` |
| **Review cadence** | `<how often>` |

## 1. Assets

What would actually hurt to lose, leak, or have altered.

| Asset | Why it matters | Consequence class |
|---|---|---|
| `<asset>` | | `<loss \| disclosure \| alteration>` |

## 2. Trust boundaries

Where control changes hands.

| Boundary | Inside | Outside | What crosses it |
|---|---|---|---|
| `<boundary>` | | | |

<Every third-party service is a boundary. Every model provider is a boundary. Every agent with
credentials sits on one.>

## 3. Adversaries and pressures

Not only attackers — anything that makes the bad outcome happen.

| Actor or pressure | Capability | Motivation | Realistic? |
|---|---|---|---|
| External attacker | | | |
| Compromised dependency | | | |
| Well-meaning insider under time pressure | | | |
| An agent acting on instructions found in content | | | |
| Accident: mistyped command, wrong environment | | | |

<The last two rows are the ones most often omitted and most often responsible.>

## 4. What could go wrong

| # | Scenario | Asset affected | Likelihood | Impact | Current control | Adequate? |
|---|---|---|---|---|---|---|
| 1 | `<what happens>` | | | | | |

## 5. Controls

| Control | Protects against | Assurance level | Evidence it is active |
|---|---|---|---|
| `<control>` | `<scenario #>` | `<declared \| validated \| enforced \| verified>` | |

**State the assurance level honestly.** A control listed as `enforced` with nothing enforcing it is
worse than an acknowledged gap.

## 6. Accepted risks

| Risk | Why accepted | Accepted by | Review date |
|---|---|---|---|

## 7. Non-goals

<What this model deliberately does not defend against, and why. A model without non-goals implies it
covers everything.>
