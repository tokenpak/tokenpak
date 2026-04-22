# tokenpak v1.3.3

## TIP-1.0 self-conformance — reference implementation proven

1.3.3 lands **Phase TIP-SC**: mechanical proof that the tokenpak reference implementation satisfies the TIP-1.0 specification its own `tokenpak-tip-validator` enforces. Live artifacts — telemetry rows, response headers, companion journal rows, capability declarations, manifests — are captured on every CI run and validated against the registry schemas. No paper-spec claims; no legacy restoration.

### What changed

- **ConformanceObserver** — single shared contract at five production chokepoints. Proxy + companion emit through the same observer; no parallel trees. Ship-safe: release-default path pays no cost when no observer is installed.
- **LoopbackProvider** — deterministic, network-free provider stub keyed by `RouteClass`. Activated only when `TOKENPAK_PROVIDER_STUB=loopback` is set.
- **Canonical manifests** — `tokenpak/manifests/tokenpak-proxy.json` (`provider-profile`) + `tokenpak-companion.json` (`client-profile`) shipped in the wheel. Capabilities arrays are one-to-one with `tokenpak.core.contracts.capabilities.SELF_CAPABILITIES_*`.
- **28-test pytest conformance suite** across Layer A (pipeline + Monitor.log emission), Layer B (companion pre_send hook), and Layer C (startup + disk-artifact round-trips).
- **`tokenpak doctor --conformance`** — operator-facing self-check. Human or `--json` output. Nine checks, ordered deterministically. Exit codes: `0` = OK, `1` = conformance failure, `2` = tooling error. Works from an installed wheel without a registry checkout (schemas vendored at `tokenpak/_tip_schemas/schemas/`).
- **`.github/workflows/tip-self-conformance.yml`** — blocking/advisory split per standard 21 §9.8. Matrix on Python 3.10 / 3.11 / 3.12 on `main`, `release/**`, `hotfix/**`, `v*` tags, and PRs targeting those bases. Advisory (single Python, `continue-on-error: true`) on every other branch.

### Status checks reviewers honor as blocking (per 21 §9.8)

- `self-conformance (blocking) / 3.10`
- `self-conformance (blocking) / 3.11`
- `self-conformance (blocking) / 3.12`

Gating is **process-enforced**, not platform-enforced. Do not merge to `main` or tag a release while any matrix status is red.

### Vendored-schema sync discipline

The wheel carries a copy of the TIP schemas at `tokenpak/_tip_schemas/schemas/`. Any TIP-MINOR change touches three surfaces in order: `tokenpak/registry:schemas/`, the `tokenpak-tip-validator` PyPI republish, and the vendored copy here. Sync checklist: `tokenpak/_tip_schemas/README.md`.

### Versioning

`1.3.002` → `1.3.3`. The 3-digit internal patch scheme (`1.2.091..095`, `1.3.001..002`) is retired as of this release; canonical 3-segment PEP 440 from here.

### No user-visible breaking changes

Every 1.3.x flow keeps working. Conformance wiring is additive; the release-default path (no observer installed) is byte-identical to 1.3.002.

### Upgrade

```bash
pip install --upgrade tokenpak
tokenpak --version                    # 1.3.3
tokenpak doctor --conformance         # verify self-conformance
tokenpak doctor --conformance --json  # machine-readable for CI
```

Full per-packet changelog: [CHANGELOG.md](CHANGELOG.md).
