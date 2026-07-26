# Profiles

Three files decide how this corpus behaves for you, and they are deliberately independent.

| File | Answers | Lives in |
|---|---|---|
| A coverage profile | *Which standards apply to me?* | `coverage/` |
| An authority profile | *Who is allowed to authorize what?* | `authority/` |
| Your adoption file | *What does my work actually do, and where have I tightened?* | `project-adoption.yaml` (copy the example) |

## 1. Why they are separate

Coverage and authority answer different questions and change at different times. A solo operator on
`starter`/`assisted` who begins delegating work moves their **authority** profile without touching
coverage. An operator who starts shipping software moves their **coverage** profile without changing
who approves anything.

Collapsing them into one "mode" setting is the mistake this design exists to avoid: it forces you to
accept more autonomy in order to get more coverage, or less coverage in order to keep control.

## 2. Resolution order

```
control baseline (controls/controls.yaml)
  → authority profile        may set values for mode_override_allowed: true controls only
    → local_tightening       may tighten anything; may not loosen a protected field
      → effective profile    every field explicitly resolved, nothing left implicit
```

**Protected controls never enter this chain from a profile.** An authority profile containing a
protected control ID is a validation error, not a strong opinion. Tighten protected controls through
`local_tightening` in your adoption file instead — that path is checked against a tightening order
and will reject a loosening attempt.

Tightening order, from loosest to tightest:

| Field | Order |
|---|---|
| `authorizer` | `none` → `delegate` → `reviewer` → `operator` |
| `authorization_type` | `none-required` → `standing-envelope` → `explicit` |
| `independence_requirement` | `none` → `separate-actor` → `independent` |

Moving right is tightening and is always allowed. Moving left on a protected control is rejected.

## 3. Writing your own profile

Copy the closest one, change the values, give it a new `id`. There is no registry to update and no
approval to seek — your copy is yours. `authority/strict.yaml` is a worked example of tightening
`supervised`.

Two things a profile cannot do, however you write it:

- It cannot alter a protected control (GOVERNANCE.md section 4, R20).
- It cannot let an executor accept its own work (R5).

## 4. Reading a compiled profile

`tools/standards.py compile` resolves everything and prints the effective profile with every field
populated. Read that, not the source files, when you want to know what actually applies — the source
files are inputs, and defaults resolved silently at read time are how people end up believing a
control is stricter than it is.

## 5. Version compatibility

Every profile carries `standards_version`. The validator checks it against the corpus version in
`GOVERNANCE.md` frontmatter and reports a mismatch rather than guessing. If you pin an older corpus,
pin it deliberately and record why.
