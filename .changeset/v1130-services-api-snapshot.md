---
---

Release-gate: reconcile the public-API snapshot with the regularized
`tokenpak.services` package.

Adding the package marker for import-contract enforcement also makes the
existing optimization, provider-usage, and routing service namespaces
discoverable to the public-API snapshot walker. Regeneration in the canonical
release environment captures 113 additive first-party symbols from those
namespaces. No public symbol is removed and no runtime behavior changes.
