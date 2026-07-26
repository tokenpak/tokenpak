---
id: BS-CONTINUITY-WORKSPACE-AND-FILE-SAFETY
layer: continuity
risk_class: high
default_coverage_profiles: [delegated-work, product-delivery, multi-agent]
control_points: [record.history-alter, data.destroy]
---

# Workspace and file safety

## Purpose

Stop automated work from damaging the environment it runs in, or from silently changing files a human
owns.

## Applies to

Any tool, script, or agent that writes to a workspace, a machine, or a shared store. **Read now if
agents modify files.**

---

## Requirements

### Ownership boundaries

**R1.** Files fall into one of three ownership classes, and the class determines what may touch them:

| Class | Who owns it | Automated write |
|---|---|---|
| **System** | The tool | Free, within its own area |
| **Managed** | The tool, on the user's behalf, by consent | Only per the managed-write rules below |
| **User** | The human | **Never without explicit per-change consent** |

**R2.** Configuration files, instruction files, and anything a human authored by hand are **user
files**. Instruction files that direct agents — however they are named — are always user files.

**R3.** **Never silently modify a user file.** Not to fix formatting, not to add a helpful default,
not to migrate a deprecated setting. Silent helpfulness in a user file is indistinguishable from
corruption from the user's point of view.

**R4.** Where a tool wants a user file changed, it **proposes**: shows exactly what would change, and
waits. The user applies it, or authorizes the tool to apply that specific change.

**R5.** Integration with a user file MUST be **additive and reversible**: append a clearly delimited
section, never restructure surrounding content. The user must be able to remove it by deleting the
delimited block.

### Managed writes

**R6.** An authorized managed write MUST:

1. Preview the exact change before applying it.
2. Back up the prior content first.
3. Be **byte-exact reversible** — restoring produces the original file, not a re-serialised
   equivalent.
4. Touch only what it declared.
5. Record what it did.

**R7.** Reformatting, reordering, or re-serialising a file you were asked to change one line of is a
violation of R6.4. It destroys the user's diff and their ability to see what actually happened.

**R8.** Protected paths MUST be skippable and skipped: a declared list the tool will not write to,
under any operation.

**R9.** Dry-run is the **default** for anything destructive or broad. Apply is explicit. A tool whose
default is to act is a tool that will act on a mistyped argument.

### Non-interactive contexts

**R10.** A tool running non-interactively MUST NOT prompt. There is nobody there; a prompt is a hang
that looks like slow work.

**R11.** In non-interactive contexts, ambiguity **fails closed** with a clear message. It does not
choose a default and continue.

**R12.** Exit status MUST reflect reality: success only when the work succeeded. Exiting zero on
partial failure defeats every automation above it.

### Installation and removal

**R13.** What a tool installs, where, and how much it will consume MUST be disclosed **before**
installation.

**R14.** Removal MUST be complete and enumerable: the tool can say what it created and remove it,
leaving user data intact unless removal was explicitly requested.

**R15.** A tool MUST NOT leave undisclosed state outside its own area — no undocumented files in
system locations, no unannounced background processes, no scheduled jobs the user did not agree to.

**R16.** Upgrades MUST NOT overwrite user-owned instantiated copies. Where a packaged default and a
user copy diverge, that is shown as a difference, never resolved by replacement.

### Working state

**R17.** Automated work SHOULD occur in an isolated copy where possible, so a mistake affects a copy.

**R18.** Before starting, verify the workspace is in a known state — no unexpected uncommitted
changes, no stale artifacts from a previous run, no half-applied prior operation.

**R19.** Interrupted work MUST leave a recoverable state. Where a partial effect cannot be left
safely, the operation completes the unit or reverses it — never leaves it half-applied silently.

---

## Evidence and acceptance

You can demonstrate: a managed write previewed, applied, and reversed byte-exactly; a dry-run that
changed nothing; a non-interactive run that failed closed instead of hanging; and a complete
uninstall that left user data untouched.

## Control points

| Control | Relevance here |
|---|---|
| `record.history-alter` | Protected. Modifying existing content, including files |
| `data.destroy` | Protected. Enumerate before destroying |

## Exceptions and stop conditions

**Stop** when: a change would touch a user file without per-change consent; a workspace is not in a
known state; a destructive operation cannot enumerate its targets; or a non-interactive run needs an
answer nobody can give.

There is no exception to R3. "The user would obviously want this" is the reasoning behind most
unwanted modifications.

## Anti-patterns

- Appending to a user's instruction file during install because it is convenient.
- Reformatting a whole config file to change one value.
- A "reversal" that produces a semantically equal but textually different file.
- Destructive default with an opt-in dry-run.
- Prompting inside a scheduled job.
- Exiting zero after a partial failure.
- An upgrade replacing the user's customised copy.
- An uninstall leaving background jobs behind.

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
