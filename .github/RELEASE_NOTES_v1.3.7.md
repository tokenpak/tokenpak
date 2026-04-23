# tokenpak v1.3.7

## TIP-SC+1 — proxy semantic invariants

1.3.7 ships **Phase TIP-SC+1**: the semantic conformance layer on top of TIP-SC. SC proved the shape of what the proxy emits; SC+1 proves that what it emits is **causally honest** about what the proxy did.

Five property-test tracks, 56 tests total, each with a causal oracle:

| # | Invariant | Status | Tests |
|---|---|---|---|
| I1 | Byte-identity on `claude-code-*` routes | **Blocking** | 20 |
| I2 | Cache-attribution honesty (Constitution §5.3) | **Blocking** | 5 |
| I3 | TTL ordering on outbound bodies | Advisory | 11 |
| I4 | DLP leak prevention | Advisory | 14 |
| I5 | Header-allowlist enforcement | **Blocking** | 6 |

### What landed

- **`ConformanceObserver.on_outbound_request`** — new callback, single dispatch chokepoint. Wired at exactly 2 sites in `proxy/server.py` (streaming + non-streaming paths). No-op when no observer installed; ship-safe.
- **`tokenpak/core/contracts/permitted_headers.py`** — canonical per-profile header allowlist + `HOP_BY_HOP` strip-set. Single source of truth.
- **56 new tests** under `tests/conformance/invariants/`. Full conformance suite (SC-06 + SC+1) = **84 tests**, all green.
- **CI**: new `self-conformance (advisory) / invariants` job alongside the existing blocking matrix. Blocking leg filters on `conformance and not advisory`; advisory leg on `conformance and advisory` with `continue-on-error: true`.

### Required status checks reviewers honor (per standard 21 §9.8)

Blocking matrix (unchanged from 1.3.6 name — content expanded):
- `self-conformance (blocking) / 3.10`
- `self-conformance (blocking) / 3.11`
- `self-conformance (blocking) / 3.12`

New advisory signal (informational; does NOT block):
- `self-conformance (advisory) / invariants`

### Why I3 + I4 ship advisory

Each depends on machinery the SC+1 phase is test-only against:

- **I3 (TTL ordering):** `prompt_builder` is the subsystem that would enforce reordering on non-byte-preserve routes. Any pre-existing bug in its reorder logic would surface here. Advisory-first lets the invariant land without forcing a `prompt_builder` fix-forward in the same phase.
- **I4 (DLP leak):** `tokenpak/security/dlp/rules.py` ships 11 rules today. I4's synthetic-secret coverage spans 5 families. If the rule set has gaps, redact-mode could theoretically miss a secret the test fabricated. Advisory-first lets us tune rule coverage without phase delay.

Both promote to blocking in a follow-up packet once stable.

### No scope expansion

No streaming-semantics work (explicitly deferred to SC+2 if warranted). No DLP rule additions. No refactor of `proxy/server.py`'s dispatch path beyond the 2-site chokepoint wire. No new production-adjacent code beyond the observer callback + canonical header contract.

### Upgrade

```bash
pip install --upgrade tokenpak
tokenpak --version                    # 1.3.7
tokenpak doctor --conformance         # structural conformance (SC-06 path)
```

The SC+1 invariants suite is exercised via `pytest tests/conformance/invariants/`; CI runs it on every tag / main push / PR to main.

Full per-packet changelog: [CHANGELOG.md](CHANGELOG.md).
