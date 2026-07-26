# TokenPak Baseline Standards

Operating standards for work you direct — your projects, your recurring workflow, your tasks, the
products you ship, and how you run your business. Applies whether the work is done by you, by a team,
or by AI agents acting on your behalf.

**New here? Read [`START-HERE.md`](START-HERE.md).** It takes about ten minutes and gets you
configured.

---

## What this is

Twenty-six standards, a control registry, two kinds of profile, twelve templates, and one validator.
The spine is domain-neutral. Software delivery is an opt-in module, not the foundation — most
operators do not ship software, and the ones who do need the spine first.

**What it is not:** enforcement, a compliance framework, or a certification. This corpus provides
`declared` and `validated` assurance only — your configuration can be checked for consistency;
nothing here bounds behaviour at runtime. `GOVERNANCE.md` section 7 states this precisely, and the pack
holds itself to the same claim discipline it asks of you.

## The idea it is built on

> Your operating mode may change **who executes** an action and **who authorizes** it. It must never
> change what is correct, what evidence is required, what gets recorded, or which protections cannot
> be waived.

Turn autonomy up and you change who signs off. The evidence requirement stays. The record stays. The
protected actions stay protected — in every profile, including the most autonomous one.

## Layout

| Path | Contents |
|---|---|
| `START-HERE.md` | Entry paths, profile selection, first-run checklist, when to stop |
| `GOVERNANCE.md` | Terms, authority model, protected actions, waivers, conformance, versioning |
| `core/` | Truth and evidence · risk and protected actions · records · intake and acceptance |
| `delegation/` | Roles · task envelopes · independent review · spend limits · concurrency |
| `commitments/` | External claims · stakeholder commitments · money and contracts |
| `information/` | Security and credentials · confidentiality, retention, disposal |
| `continuity/` | Incidents and escalation · backup and recovery · workspace safety |
| `domains/` | Opt-in modules. Ships with `software-delivery/`; `README.md` shows how to write your own |
| `controls/` | `controls.yaml` — the canonical registry. Mode tables are generated from it |
| `profiles/` | Coverage, authority, and your adoption file |
| `schemas/` | JSON Schemas for the above |
| `templates/` | Decisions, tasks, acceptance, waivers, incidents, release logs, and more |
| `tools/` | `standards.py` — validate, compile, generate, check-generated |

## Using the validator

```bash
python tools/standards.py validate                      # schema + cross-file semantic checks
python tools/standards.py compile --authority supervised --coverage starter
python tools/standards.py generate                      # rewrite the generated mode tables
python tools/standards.py check-generated               # fail if generated output drifted
```

Requires PyYAML. `jsonschema` is optional — without it, schema validation reports itself as **not
run** rather than passing, because a check that did not run is not a pass.

Read the **compiled** profile, not the source files, when you want to know what applies. Compilation
resolves every field explicitly; defaults silently applied at read time are how people come to
believe a control is stricter than it is.

## Adopting it

1. Export or copy this directory into your project. It becomes yours.
2. Pick a coverage profile and an authority profile.
3. Copy `profiles/project-adoption.example.yaml` → `project-adoption.yaml` and fill it in.
4. Enumerate your own protected actions. This is the step people skip and the one that matters most.
5. Run `validate`.

Nothing is applied to your project automatically, and nothing here ever writes to your agent
instruction files. Your copy is yours to change — `GOVERNANCE.md` section 11 gives you the lifecycle for
amending it, and section 11 R39 asks only that you supersede rather than delete, so you can still see why
you changed your mind.

## Versioning

The corpus carries one version, in `GOVERNANCE.md` frontmatter, and nowhere else. Profiles declare
`standards_version`; the validator reports a mismatch rather than guessing.

## Licence

This corpus is distributed under the licence of the repository it ships with — see that repository's
`LICENSE` file.

**For your own project, this corpus will not choose a licence for you.** If `validate` finds no
licence, it reports that fact and stops there: what licence your work carries determines whether and
how anyone else may use it, and that is a decision with legal consequence that belongs to you, not
to tooling (`GOVERNANCE.md` R47–R49, `domains/software-delivery/documentation-and-presentation.md`
R20–R24). If you have already settled the question, record it as a standing order and the notice
stops (`GOVERNANCE.md` section 13b).
