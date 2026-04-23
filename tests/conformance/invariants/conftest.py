"""SC+1 test harness — direct observer-driven assertion tests.

SC2-02 wires ``notify_outbound_request`` at two dispatch chokepoints
in ``proxy/server.py`` (streaming + non-streaming paths). These tests
assert the invariant ASSERTIONS work correctly against synthetic
observer captures — the wiring itself is proven separately by
``test_wiring_smoke.py`` (static audit + a live dispatch probe) and
by code review on SC2-02's two one-line additions.

Each test:
1. Installs a conformance observer via the SC-06 ``conformance_observer``
   fixture.
2. Calls ``notify_outbound_request(route_class, url, method, headers,
   body)`` directly, simulating what the proxy would fire.
3. Asserts the captured five-tuple satisfies the invariant under test.

Scope discipline: these tests prove the ASSERTION LOGIC, not a full
HTTP round trip. SC2-10's CI wiring adds the smoke probe; the
assertions here are what catches regressions.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

import pytest


@pytest.fixture
def fire_outbound(conformance_observer):
    """Return a callable that fires notify_outbound_request and returns the captured event.

    Usage::

        def test_foo(fire_outbound):
            captured = fire_outbound(
                route_class='claude-code-tui',
                url='https://api.anthropic.com/v1/messages',
                method='POST',
                headers={'authorization': 'Bearer x'},
                body=b'{"model":"claude"}',
            )
            assert captured['body'] == b'{"model":"claude"}'
    """
    from tokenpak.services.diagnostics import conformance as _conformance

    def _fire(
        *,
        route_class: str,
        url: str,
        method: str = "POST",
        headers: Mapping[str, str] | None = None,
        body: bytes = b"",
    ) -> Dict[str, Any]:
        _conformance.notify_outbound_request(
            route_class, url, method, dict(headers or {}), body
        )
        # The SC-06 conformance_observer fixture stores events under a
        # dict keyed by kind. We added 'outbound' during the SC+1
        # harness-extension below; this helper returns the latest.
        out = conformance_observer.get("outbound", [])
        assert out, "expected at least one captured outbound request"
        return out[-1]

    return _fire


# Extend the SC-06 conformance_observer fixture's observer class to
# capture the SC+1 on_outbound_request callback. We do this via a
# pytest fixture override that wraps the parent's captured dict with a
# supplemental 'outbound' list.
#
# Implementation detail: SC-06's ``conformance_observer`` fixture
# installs an anonymous observer that doesn't know about
# on_outbound_request. Trying to extend it without touching SC-06 is
# fragile, so we ship our own SC+1 observer fixture here with parity
# keys (telemetry, headers, journal, capabilities, outbound).
@pytest.fixture
def conformance_observer():  # noqa: F811 — deliberate override for SC+1
    """SC+1 observer fixture — superset of SC-06's four capture kinds
    plus the SC+1 ``outbound`` kind."""
    from tokenpak.services.diagnostics import conformance as _conformance

    captured: Dict[str, list] = {
        "telemetry": [],
        "headers": [],
        "journal": [],
        "capabilities": [],
        "outbound": [],
    }

    class _Obs:
        def on_telemetry_row(self, row):
            captured["telemetry"].append(dict(row))

        def on_response_headers(self, headers, direction):
            captured["headers"].append((direction, dict(headers)))

        def on_companion_journal_row(self, row):
            captured["journal"].append(dict(row))

        def on_capability_published(self, profile, caps):
            captured["capabilities"].append((profile, list(caps)))

        def on_outbound_request(self, route_class, url, method, headers, body):
            captured["outbound"].append({
                "route_class": route_class,
                "url": url,
                "method": method,
                "headers": dict(headers),
                "body": body,
            })

    uninstall = _conformance.install(_Obs())
    try:
        yield captured
    finally:
        uninstall()
