# tokenpak capsule — standard conformance fixture

Session memory seed used by the SC-05/SC-06 companion enrichment scenario.
Kept small + deterministic so fixtures stay reviewable.

## Project context
- Name: TokenPak
- Profiles claimed: tip-proxy, tip-companion (Constitution §13.3)
- Active phase: TIP-SC (2026-04-22) — reference-implementation self-conformance

## Known contracts
- Byte-preserve on every claude-code-* route.
- cache_origin ∈ {proxy, client, unknown}; never over-claim.
- Wire-side telemetry = source of truth; journal = local UX state.
