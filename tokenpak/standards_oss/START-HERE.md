# TokenPak Baseline Standards

A starting set of operating standards for work you direct — your own projects, your recurring
workflow, individual tasks, the products you ship, and the way you run your business. It applies
whether the work is done by you, by a team, or by AI agents acting on your behalf.

This corpus is **yours once you export it.** Nothing here is applied to your project automatically.
Nothing here writes to your agent instruction files. Change anything you disagree with — the pack
includes the governance for amending your own copy.

**Status of this corpus:** guidance and machine-checkable declarations. It is not enforcement, not a
compliance framework, and not a certification. See `GOVERNANCE.md` section 7 for exactly what "conformance"
does and does not mean here.

---

## 1. Two ways in

**You are adopting this for work you control.** Read section 2, pick your profiles (section 3), declare your
capabilities (section 4), then follow the reading path in section 6.

**You are joining work someone else already governs.** Read `GOVERNANCE.md` sections 2-4 (terms, authority
model, protected actions), then the compiled profile for the work you are joining
(`profiles/README.md` section 4 explains how to read it). Do not re-select profiles — the operator owns
that choice.

---

## 2. The one idea that makes the rest work

> **Your operating mode may change who executes an action and who authorizes it. It must never
> change what is correct, what evidence is required, what gets recorded, or which protections
> cannot be waived.**

Most operating disciplines fail by mixing these up: as autonomy increases, the standard of
correctness quietly drops with it. Here they are separate dials. Turn autonomy up and you change
*who signs off*. The evidence requirement stays. The record stays. The protected actions stay
protected.

Everything else in this corpus is an application of that sentence.

---

## 3. Pick two profiles

They are independent. One says **which standards apply to you**; the other says **who is allowed to
authorize what**.

### 3a. Coverage profile — what applies

| Profile | Choose it when | Adds |
|---|---|---|
| `starter` | You are beginning, or the work is small and personal | The eight foundations in section 6 — truth, risk, records, acceptance, spend, credentials, incidents |
| `delegated-work` | You direct recurring work, especially to AI agents | Roles, task envelopes, independent review, spend limits, concurrency |
| `product-delivery` | You ship something other people install, run, or depend on | The software-delivery domain module, plus external claims and commitments |
| `multi-agent` | Several executors work concurrently on shared resources | Everything, with concurrency and shared-resource control mandatory |

Profiles are cumulative: each includes the one above it. Files live in `profiles/coverage/`.

**Coverage adds; it never subtracts.** No coverage profile can switch off the universal invariants,
the protected-action rules, or the record-keeping obligations. If you declare that you never publish
externally and then publish, the obligation attaches to the act — not to your declaration.

### 3b. Authority profile — who authorizes

| Profile | Meaning |
|---|---|
| `assisted` | You approve every substantive action. Agents draft, recommend, and prepare. |
| `supervised` *(default)* | Defined decision points route to you or a designated reviewer. Everything else proceeds and leaves a record. |
| `bounded-autonomous` | Work proceeds on evidence with batched notification instead of approval. **Bounded** is load-bearing: the protected actions in `GOVERNANCE.md` section 4 stay human in every profile. There is no unbounded profile and there will not be one. |

`profiles/authority/strict.yaml` ships as an example of tightening `supervised` further — it requires
verification by an independent party for critical decisions.

Files live in `profiles/authority/`. A profile sets defaults **per control point**, so you can run
`bounded-autonomous` broadly and still require yourself on, say, external commitments.

---

## 4. Declare what you actually do

The corpus needs to know which obligations attach to you. It asks you, rather than guessing:
`profiles/project-adoption.example.yaml` is the file you fill in.

You declare, for each capability: `yes`, `no`, or `unknown` — plus who owns the answer and where the
evidence is.

```yaml
capabilities:
  publishes_externally:   { value: yes,     owner: "you@example.com", evidence: "release notes" }
  handles_third_party_data: { value: unknown, owner: "you@example.com", evidence: null }
  authorizes_payments:    { value: no,      owner: "you@example.com", evidence: null }
```

Three rules about declarations, and they matter:

1. **`unknown` on a protected capability blocks the action.** Not "warns" — the validator reports an
   error and the standard says you must not proceed. Resolve the declaration first.
2. **A contradiction is an error, not a preference.** If you declare you never publish externally and
   your workspace contains a publishing pipeline, that is a validation error to resolve, not a
   warning to dismiss.
3. **Silence never proves absence.** No detection finding absence of a signal is ever treated as
   evidence that the capability is absent. Only your declaration speaks.

---

## 5. First run — six steps

```
1. Export the corpus into your project        (it becomes yours; nothing overwrites)
2. Copy a coverage profile and an authority profile
3. Fill in the adoption file (section 4) — capabilities, owner, budgets
4. Enumerate YOUR protected actions           GOVERNANCE.md section 4 — the concrete list for your work
5. Write down where escalation goes           who to reach, how, and what happens if they don't answer
6. Run validation                             resolves your effective profile and reports contradictions
```

Step 4 is the one people skip and the one that matters most. The corpus gives you categories;
only you know which concrete actions in *your* work fall into them. An unnamed protected action is
an unprotected one.

Step 5 has a specific failure mode worth naming: **the escalation target is not a blocker by
default.** If your escalation path is "ask the operator" and the operator is asleep, the correct
behavior is defined in advance — proceed within a stated envelope, or stop cleanly — never "wait
indefinitely" and never "guess and continue."

---

## 6. Reading path

Eight units. Each says whether you read it now, configure it now, or wait until something triggers
it. This is the onboarding order, not the file layout.

| # | Unit | What you do | Where |
|---|---|---|---|
| 1 | **Authority and modes** | Configure now | `GOVERNANCE.md` sections 2-6 |
| 2 | **Truth and evidence** | Read now | `core/truth-and-evidence.md` |
| 3 | **Risk, reversibility, protected actions** | Read now, then configure section 4 list | `core/risk-and-protected-actions.md` |
| 4 | **Records and auditability** | Configure now | `core/records-and-auditability.md` |
| 5 | **Intake and acceptance** | Read now | `core/work-intake-and-acceptance.md` |
| 6 | **Delegation and independent review** | Read now if agents or others do the work | `delegation/` |
| 7 | **Money, secrets, and other people's data** | Configure now | `commitments/`, `information/` |
| 8 | **When things go wrong** | Read now — this is not a "later" chapter | `continuity/incidents-and-escalation.md` |

You can complete all six first-run steps through these eight units without reading every standard in
your coverage profile. The rest applies in the background or when triggered, and each standard states
which it is under **Applies to**.

---

## 7. Layout

```
START-HERE.md              this file
GOVERNANCE.md              terms, authority model, protected actions, waivers, conformance, versioning

core/                      domain-neutral, always applies
delegation/                directing work to others — people or agents
commitments/               what you promise to the outside world, and money
information/               secrets, credentials, and data you hold (including other people's)
continuity/                incidents, escalation, recovery, and not breaking the workspace

domains/                   opt-in modules that assume specific machinery
  software-delivery/       for work that ships installable, versioned software
  README.md                how to write your own module for your kind of work

controls/controls.yaml     the canonical control-point registry — the single source of authority values
profiles/                  coverage, authority, and your adoption file
schemas/                   JSON Schemas for the above
templates/                 fill-in artifacts: decisions, tasks, acceptance, waivers, incidents, …
tools/                     one validator, four functions
```

**Where values live matters.** Prose describes behavior. `controls/controls.yaml` holds the authority
values. Profiles hold defaults. Generated tables hold nothing — they are output. If prose and data
disagree, that is a defect to fix, never a precedence question to resolve. See `GOVERNANCE.md` section 8.

---

## 8. When to stop and escalate

Stop, do not improvise, and escalate when:

- An action is protected and you lack fresh, specific authorization for **this** action.
- A capability declaration relevant to a protected action is `unknown`.
- Measurement you depend on has failed, and proceeding would mean asserting a number you cannot
  support.
- You are about to do something you cannot undo, and no one has authorized this instance of it.
- The standards contradict each other, or a standard contradicts an instruction you were given.
- You are about to waive a requirement and cannot name the authority, the expiry, and the
  compensating control.

Improvisation at these points is the single most expensive failure mode this corpus exists to
prevent. Stopping is always in scope and never requires permission.

---

## 9. Amending your copy

Your copy is yours. `GOVERNANCE.md` section 11 gives you the lifecycle: propose → decide → record →
supersede. Two rules make it survivable:

- **Supersede; do not delete.** A superseded rule stays readable with a pointer to what replaced it.
  You will need to know why you changed your mind.
- **You may tighten; you may not quietly loosen.** Local changes can add requirements or narrow
  authority freely. Weakening a protected-action rule requires a recorded decision naming who
  authorized it — the validator will not let it pass silently.

---

*Version: see `GOVERNANCE.md` frontmatter — the corpus carries one version, in one place.*
