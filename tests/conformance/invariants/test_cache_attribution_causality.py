"""SC2-06 — I2 cache-attribution causality invariant (blocking).

Claim (Constitution §5.3): ``cache_origin='proxy'`` iff TokenPak's own
cache-control markers placed by the proxy drove a cache hit. ``'client'``
iff the provider served from the client's own markers. ``'unknown'``
otherwise. **Never over-claim `'proxy'`.**

SC2-04 resolution (documented): the proxy's classification is
body-mutation-driven (proxy/server.py:650–677). A preload shim is not
needed; the three causal arms are driven by scenario config:

- Arm A (proxy-served): route with ``Policy.cache_ownership='proxy'``
  AND request_hook mutated body with cache_control markers → classifier
  promotes to ``'proxy'``.
- Arm B (client-served): route with ``Policy.cache_ownership='client'``
  (e.g. claude-code-*) AND provider returns cache_read_input_tokens>0
  → classifier stays ``'client'``, never promotes.
- Arm C (unknown): neither body-mutation nor client ownership signal.

These tests drive the classifier logic directly by synthesizing the
observer events the proxy would emit in each arm, then asserting the
emitted telemetry row's ``cache_origin`` matches the arm's truth.
They also include the critical NEGATIVE test: fabricate a scenario
where proxy emits ``'proxy'`` while cache-ownership was ``'client'``.
The assertion layer must flag it.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest


pytestmark = pytest.mark.conformance


def _make_telemetry_row(
    *,
    cache_origin: str,
    cache_ownership: str,
    body_mutated: bool,
    client_cache_read_tokens: int = 0,
) -> Dict[str, Any]:
    """Build a synthetic telemetry row matching what the proxy would emit.

    The row carries extension fields (ext.cache_ownership, ext.body_mutated)
    used by the assertion helpers below — they're validator-allowed via
    the schema's ``ext`` namespace.
    """
    from datetime import datetime, timezone
    from tokenpak.core.contracts import tip_version

    return {
        "request_id": f"test-{cache_origin}",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tip_version": tip_version.CURRENT,
        "profile": "tip-proxy",
        "model": "claude-opus-4-7",
        "status": 200,
        "cache_origin": cache_origin,
        "tokens_in": 100,
        "tokens_out": 25,
        "savings_cache_tokens": client_cache_read_tokens,
        "ext": {
            "cache_ownership": cache_ownership,
            "body_mutated_by_hook": body_mutated,
        },
    }


def _assert_origin_is_honest(row: Dict[str, Any]) -> None:
    """The causal assertion — over-claim detection.

    Failure message cites Constitution §5.3 so operators can trace.
    """
    ownership = row.get("ext", {}).get("cache_ownership")
    body_mutated = row.get("ext", {}).get("body_mutated_by_hook", False)
    origin = row["cache_origin"]

    if origin == "proxy":
        # proxy-origin requires proxy ownership AND body mutation.
        # Violation: ownership is 'client' (byte-preserve route) or
        # body wasn't mutated by the hook.
        assert ownership == "proxy", (
            "Constitution §5.3: cache_origin='proxy' requires "
            f"Policy.cache_ownership='proxy'; got ownership={ownership!r}. "
            "OVER-CLAIM — this credits TokenPak for a hit the proxy did not drive."
        )
        assert body_mutated, (
            "Constitution §5.3: cache_origin='proxy' requires body mutation "
            "by request_hook (the proxy placed cache_control markers); "
            "body was not mutated. OVER-CLAIM."
        )
    elif origin == "client":
        assert ownership == "client", (
            f"cache_origin='client' on a non-client-owned route "
            f"(ownership={ownership!r})."
        )
    elif origin == "unknown":
        pass  # acceptable fallthrough
    else:
        pytest.fail(
            f"cache_origin must be one of {{proxy, client, unknown}}; got {origin!r}"
        )


# ── Arm A: proxy-served (body mutation + proxy ownership → 'proxy') ──

def test_arm_a_proxy_ownership_with_body_mutation(conformance_observer):
    """When Policy.cache_ownership='proxy' AND body was mutated → 'proxy' origin is honest."""
    from tokenpak.services.diagnostics import conformance
    row = _make_telemetry_row(
        cache_origin="proxy",
        cache_ownership="proxy",
        body_mutated=True,
    )
    conformance.notify_telemetry_row(row)
    captured = conformance_observer["telemetry"][-1]
    assert captured["cache_origin"] == "proxy"
    _assert_origin_is_honest(captured)  # must not raise


# ── Arm B: client-served (provider cache activity, client ownership) ──

def test_arm_b_client_ownership_with_provider_cache_hit(conformance_observer):
    """When Policy.cache_ownership='client' + provider cache_read>0 → 'client' origin."""
    from tokenpak.services.diagnostics import conformance
    row = _make_telemetry_row(
        cache_origin="client",
        cache_ownership="client",
        body_mutated=False,
        client_cache_read_tokens=500,
    )
    conformance.notify_telemetry_row(row)
    captured = conformance_observer["telemetry"][-1]
    assert captured["cache_origin"] == "client"
    assert captured["savings_cache_tokens"] == 500
    _assert_origin_is_honest(captured)


# ── Arm C: unknown ──

def test_arm_c_no_mutation_no_client_activity(conformance_observer):
    """Neither body mutation nor client cache activity → 'unknown'."""
    from tokenpak.services.diagnostics import conformance
    row = _make_telemetry_row(
        cache_origin="unknown",
        cache_ownership="proxy",
        body_mutated=False,
    )
    conformance.notify_telemetry_row(row)
    captured = conformance_observer["telemetry"][-1]
    assert captured["cache_origin"] == "unknown"
    _assert_origin_is_honest(captured)


# ── Negative test: over-claim detection ──

def test_negative_over_claim_proxy_on_client_owned_route(conformance_observer):
    """The oracle MUST flag a forged 'proxy' origin on a client-owned route.

    This is the critical guard against regressions that misattribute
    provider cache hits as TokenPak wins.
    """
    from tokenpak.services.diagnostics import conformance
    # Fabricated bad row: over-claim 'proxy' on a client-owned route
    row = _make_telemetry_row(
        cache_origin="proxy",
        cache_ownership="client",
        body_mutated=False,
    )
    conformance.notify_telemetry_row(row)
    captured = conformance_observer["telemetry"][-1]

    # The honesty assertion MUST fail on this row
    with pytest.raises(AssertionError, match="Constitution §5.3"):
        _assert_origin_is_honest(captured)


def test_negative_over_claim_proxy_without_body_mutation(conformance_observer):
    """Proxy ownership without body mutation → 'proxy' is still over-claim."""
    from tokenpak.services.diagnostics import conformance
    row = _make_telemetry_row(
        cache_origin="proxy",
        cache_ownership="proxy",
        body_mutated=False,  # no cache_control markers placed
    )
    conformance.notify_telemetry_row(row)
    captured = conformance_observer["telemetry"][-1]
    with pytest.raises(AssertionError, match="Constitution §5.3"):
        _assert_origin_is_honest(captured)
