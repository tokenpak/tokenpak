---
topic: License / legal surface
slug: license
status: ACTIVE
canonical_value: "Apache-2.0"
forbidden_values: ["MIT", "GPL", "GPL-2.0", "GPL-3.0", "AGPL", "AGPL-3.0", "LGPL", "BSD", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Unlicense", "MPL", "MPL-2.0"]
allowlist_contexts:
  - "Third-party comparison tables or matrices that describe another tool's license."
  - "Private commercial package license declarations outside the public OSS repository."
  - "Transitive dependency licenses in lockfiles, vendored trees, or generated dependency metadata."
  - "Historical or changelog references that document a prior license value without asserting it as current."
canonical_authority: LICENSE
change_class: A
enforcement: .github/workflows/license-check.yml
guard_mode: blocking
---

# License Decision

TokenPak public OSS package and plugin metadata declare `Apache-2.0`.

Superseded license values are blocked when they appear as current first-party
license declarations. The guard intentionally does not block third-party
comparison rows, dependency lockfile metadata, vendored files, commercial
private packages, or historical notes.
