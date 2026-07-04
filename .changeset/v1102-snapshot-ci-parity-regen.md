---
---

Release-gate: regenerate the public-API snapshot in the canonical release
environment for v1.10.2.

The prior regeneration (the 1.10.1 reconcile) ran against a stale installed
package instead of the source tree, so the committed snapshot was blind to the
bounded-retry surface and self-consistently passed local checks while failing
in CI. Regenerated under the release pipeline's install shape (editable
install, union of non-agent-framework extras, `TOKENPAK_SNAPSHOT_GEN=1`,
Python 3.12), verified by reproducing the CI drift byte-for-byte before the
regeneration and a clean check after it.

Snapshot corrections beyond the intended transport-reliability delta (which is
described in `proxy-transport-reliability-api-snapshot.md`):

- Drops `tokenpak.companion.recall.ranking.RankedPak` and
  `tokenpak.companion.recall.ranking.rank_paks` — these records came from the
  stale environment; the module does not exist in the released source tree and
  the symbols were never part of the released public surface. Snapshot-record
  correction only; no source change and no runtime impact.
- Normalizes the `tokenpak.sdk.crewai.examples.basic_usage` import-error
  record to the canonical environment-independent shape.

Compatibility preserved (owner-ruled for this PATCH release):
`tokenpak.proxy.server.MAX_UPSTREAM_RETRIES` is retained as a deprecated,
non-authoritative compatibility alias so existing imports continue to work —
retry behavior is governed by `UpstreamRetryPolicy`, which reads the
`TOKENPAK_UPSTREAM_RETRIES` environment variable itself (the supported
operator control; operator-facing behavior unchanged). A regression test pins
the alias. No public symbol is removed by this release.
