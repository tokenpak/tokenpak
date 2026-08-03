# TIP Optimization Schemas (v1)

The TokenPak Integrity Protocol (TIP) optimization pipeline is defined by six
versioned, machine-readable JSON Schemas (draft-07). They are the vocabulary a
third-party implementation needs to produce or consume TIP optimization
artifacts: request route classes, content-fidelity tiers, cache and compression
policy, capability labels, and the per-request optimization trace.

This page is the citable index for those schemas: where each one lives, what it
governs, and what a conforming producer or consumer must do.

---

## Citable schema URLs

Each schema declares a canonical, versioned `$id`. The `$id` is the stable
identifier to cite when referencing a schema from documentation, manifests, or
another schema.

| Schema | Canonical `$id` |
|---|---|
| Route class | `https://docs.tokenpak.ai/schemas/tip/tip-route-class-v1.json` |
| Fidelity policy | `https://docs.tokenpak.ai/schemas/tip/tip-fidelity-policy-v1.json` |
| Cache policy | `https://docs.tokenpak.ai/schemas/tip/tip-cache-policy-v1.json` |
| Compression policy | `https://docs.tokenpak.ai/schemas/tip/tip-compression-policy-v1.json` |
| Capability set | `https://docs.tokenpak.ai/schemas/tip/tip-capabilities-v1.json` |
| Optimization trace | `https://docs.tokenpak.ai/schemas/tip/tip-optimization-trace-v1.json` |

The schema files themselves are maintained in the open-source repository at
[`tokenpak/tip/schemas/`](https://github.com/tokenpak/tokenpak/tree/main/tokenpak/tip/schemas)
and ship inside the `tokenpak` Python package. A version-pinned copy of any
schema can be retrieved from any release tag:

```text
https://raw.githubusercontent.com/tokenpak/tokenpak/<tag>/tokenpak/tip/schemas/<filename>
```

For example, the route-class schema as shipped in v1.16.0:

```text
https://raw.githubusercontent.com/tokenpak/tokenpak/v1.16.0/tokenpak/tip/schemas/tip-route-class.v1.json
```

---

## Versioning and stability

- The `v1` in each filename and `$id` is the schema revision. **Published v1
  schemas are frozen:** no field will be removed, repurposed, or made stricter
  under a v1 URL.
- A breaking change ships as a new schema revision (`-v2`) at a new URL. The
  v1 URL and its meaning remain valid indefinitely.
- Schema revisions are independent of the `TIP-<MAJOR>.<MINOR>` protocol
  version label. All six v1 schemas belong to the TIP-1.0 surface.

---

## Conformance

Throughout this page, a **producer** is any component that emits an instance of
a schema (a classifier, a policy resolver, an optimizing proxy); a **consumer**
is any component that reads one (an optimization stage, a telemetry sink, a
dashboard). In every case the baseline requirement is the same: producers must
emit instances that validate against the schema, and consumers must accept any
valid instance.

The statements below add the semantic requirements that validation alone cannot
express.

### Route class — `tip-route-class.v1.json`

The semantic request taxonomy: a single string that classifies what kind of
request is being optimized (for example `code_edit`, `debugging`,
`status_check`).

**Conformance.**

- A producer must emit exactly one value from the enum, and must emit
  `unknown` when classification fails or is unavailable — never an invented
  value.
- A consumer must treat an absent or unrecognized route class as `unknown` and
  must not reject the request because of it.
- Route-class-driven behavior (cache policy, fidelity tier) must degrade to the
  most conservative applicable policy when the class is `unknown`.

### Fidelity policy — `tip-fidelity-policy.v1.json`

The content-preservation tier for an optimization stage: one of
`lossless_required`, `semantic_safe`, `aggressive_ok`, `cache_response_safe`,
or `no_optimize`.

**Conformance.**

- A producer must emit exactly one tier per request or stage decision.
- A consumer performing an optimization must check the active tier before
  mutating content, and must not apply any transformation the tier forbids —
  under `lossless_required`, protected spans are preserved byte-for-byte and
  response reuse is prohibited.
- A consumer that cannot satisfy the required tier must skip the optimization
  and record the bypass; degrading fidelity is never a permitted fallback.
- Under `no_optimize`, all optimization stages must be bypassed.

### Cache policy — `tip-cache-policy.v1.json`

The per-request cache behavior contract: scope, TTL, similarity threshold, and
the reuse permissions. The schema is closed (`additionalProperties: false`).

**Conformance.**

- A producer must not emit fields outside the schema; absent fields take the
  schema's declared defaults.
- A consumer must not reuse a cached response unless `allow_response_reuse` is
  `true`, and must not serve cached context when `allow_context_reuse` is
  `false`.
- A consumer must honor `scope` (cache entries must never leak across the
  declared scope boundary) and `ttl_seconds`.
- When `bypass_reason` is set or `enabled` is `false`, cache lookup must be
  skipped and the reason carried through to the optimization trace.

### Compression policy — `tip-compression-policy.v1.json`

The per-request compression contract: which recipes may run, the target ratio,
and which content must survive untouched. The schema is closed
(`additionalProperties: false`).

**Conformance.**

- A consumer must preserve, verbatim, every span whose type appears in
  `protected_span_types`.
- When `preserve_exact_blocks` is `true`, fenced code blocks and quoted exact
  output must be treated as lossless zones.
- When `bypass_reason` is set or `enabled` is `false`, compression must not
  run, and the reason must be carried through to the optimization trace.
- A producer must reference recipes by stable identifier in `recipe_ids`;
  consumers must skip (not fail on) recipe identifiers they do not recognize.

### Capability set — `tip-capabilities.v1.json`

The set of capability labels a format or platform adapter declares: a unique
array of strings matching `tip.<group>.<feature>` or `ext.<vendor>.<feature>`.

**Conformance.**

- A producer must declare only capabilities it actually implements, using the
  exact label grammar; the `tip.` namespace is reserved for labels defined by
  the protocol, and vendor extensions must use `ext.<vendor>.<feature>`.
- Labels are compared as exact strings; there is no implied hierarchy or
  wildcard matching.
- A consumer must ignore labels it does not recognize rather than fail, and
  must not infer a capability that was not declared.

### Optimization trace — `tip-optimization-trace.v1.json`

The per-request observability record: which stages ran, what they did, token
counts before and after, cache outcome, and savings attribution. The schema is
closed (`additionalProperties: false`); `request_id`, `model`, and
`adapter_format` are required.

**Conformance.**

- A producer must emit at most one trace per optimized request, and token
  counts must be measured values, not estimates presented as measurements.
- In `savings` entries, `credited_to_tokenpak` must be `true` only for savings
  caused by an optimization decision the emitting component actually made —
  provider-side effects it merely observed must not be claimed.
- Every stage that was attempted must appear in `stages`, including skipped
  stages with their `skip_reason`.
- A consumer must tolerate the absence of any optional block (`cache`,
  `compression`, `savings`, `recommendations`) and must not treat a missing
  block as a claim that the stage ran.
