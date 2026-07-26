---
id: BS-SWDEL-DEPENDENCIES-AND-SUPPLY-CHAIN
layer: domains/software-delivery
risk_class: high
default_coverage_profiles: [product-delivery, multi-agent]
control_points: [work.accept, publish.external-irreversible, access.credential-change]
---

# Dependencies and supply chain

## Purpose

Take responsibility for code you did not write but do ship, and protect the path between your source
and your users.

## Applies to

Any project with third-party dependencies or an automated publishing path. **Read now if you publish
software.**

---

## Requirements

### Taking on a dependency

**R1.** Adding a dependency is `work.accept` — you are accepting code you did not write into work you
are accountable for.

**R2.** Before adding, know: what it does, who maintains it, its licence, what it pulls in
transitively, and what happens if it is abandoned.

**R3.** Prefer fewer, well-understood dependencies. Each one is a surface, a licence obligation, an
upgrade burden, and a party who can change your behaviour.

**R4.** Licences are checked before adoption, not before release. Discovering an incompatible licence
at release is discovering it too late.

### Pinning and reproducibility

**R5.** Builds are **reproducible**: the same source produces the same result, with dependency
versions resolved from a committed record, not from whatever is current.

**R6.** Released artifacts build from **pinned** versions. Floating versions mean your release
contains code nobody chose.

**R7.** Upgrades are deliberate changes with their own evidence, reviewed like any other change.

**R8.** Upgrading MUST include reading what changed. A minor version bump can change defaults —
which is a breaking change by `versioning-compatibility-deprecation.md` R2, whether or not the
publisher labelled it one.

### Integrity

**R9.** Verify what you fetch: checksums or signatures where available. An unverified fetch is trust
extended to whoever controls the network path.

**R10.** Know what is in the artifact you ship. Verify that the built package contains what you
intended and nothing you did not — no stray files, no development-only material, no credentials.

**R11.** Publish signed or verifiable artifacts where the ecosystem supports it, and say how users can
verify.

**R12.** The build path is part of the product. A compromised build system publishes signed malware
that verifies correctly.

### The publishing path

**R13.** Publishing credentials are protected (`access.credential-change`), scoped to publishing only,
and held by the minimum number of actors.

**R14.** Prefer a publishing mechanism with **no long-lived credential** where the ecosystem supports
it. A credential that does not exist cannot leak.

**R15.** Automated publishing MUST be reachable only from the intended path. An automation that can be
triggered against an arbitrary reference can publish arbitrary code — verify what actually gates
yours, rather than assuming the documented trigger is the only one.

**R16.** Automation that performs an irreversible publish MUST verify it is operating on the intended,
promoted, verified state — not merely on state that looks structurally valid.

**R17.** Review what your automation can do on the assumption that its triggering conditions will
eventually be met in a way you did not anticipate.

### Vulnerabilities

**R18.** Scan for known vulnerabilities on a stated cadence, and state whether the scan is blocking or
advisory.

**R19.** A finding gets a disposition: fixed, mitigated, or accepted with a reason and a review date.
Unresolved findings accumulating in a dashboard is not a process.

**R20.** Where you cannot fix quickly, say what you have done. Silence about a known vulnerability in
shipped software is a claim that there is none.

---

## Evidence and acceptance

You can produce: your dependency list with licences, the pinned versions in the last release, how
artifacts are verified, who can publish and with what credential, and the current vulnerability
findings with dispositions.

## Control points

| Control | Relevance here |
|---|---|
| `work.accept` | Adopting a dependency |
| `publish.external-irreversible` | The automated publishing path |
| `access.credential-change` | Publishing credentials — protected |

## Exceptions and stop conditions

**Stop** when: a build cannot be reproduced; an artifact contains something unexpected; a publishing
credential may be exposed; or automation could publish from a state that was never promoted.

## Anti-patterns

- A dependency added to save twenty lines, pulling in forty packages.
- Floating versions in a released artifact.
- Upgrading without reading what changed.
- Licence check at release time.
- A publishing token in a widely-readable configuration.
- Publishing automation triggerable from any reference.
- A vulnerability dashboard nobody dispositions.
- Shipping a package containing development credentials nobody looked for.

## Change record

| Version | Change |
|---|---|
| 1.0.0 | Initial. |
