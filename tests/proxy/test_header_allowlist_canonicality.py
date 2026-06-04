"""Guard against re-introducing a dual definition of CLAUDE_CODE_HEADER_ALLOWLIST.

Before this guard, `tokenpak.proxy.passthrough` held a 7-entry tuple and
`tokenpak.proxy.headers` held the canonical 19-entry frozenset; the shim at
`tokenpak.core.runtime.proxy` re-exported the wrong one, so any caller that
imported from the shim silently lost x-stainless-* and x-app forwarding.
"""

from tokenpak.core.runtime.proxy import (
    CLAUDE_CODE_HEADER_ALLOWLIST as SHIM_ALLOWLIST,
)
from tokenpak.proxy import CLAUDE_CODE_HEADER_ALLOWLIST as PKG_ALLOWLIST
from tokenpak.proxy.headers import CLAUDE_CODE_HEADER_ALLOWLIST as CANONICAL


def test_shim_reexports_canonical_object():
    assert SHIM_ALLOWLIST is CANONICAL


def test_package_export_is_canonical_object():
    assert PKG_ALLOWLIST is CANONICAL


def test_passthrough_no_longer_defines_allowlist():
    from tokenpak.proxy import passthrough

    assert not hasattr(passthrough, "CLAUDE_CODE_HEADER_ALLOWLIST")


def test_allowlist_size_is_canonical_18():
    assert len(CANONICAL) == 18


def test_required_billing_routing_headers_present():
    for h in (
        "x-stainless-arch",
        "x-stainless-os",
        "x-stainless-runtime",
        "x-stainless-runtime-version",
        "x-stainless-package-version",
        "x-stainless-lang",
        "x-app",
        "accept",
        "content-type",
        "x-claude-code-session-id",
        "user-agent",
    ):
        assert h in CANONICAL, f"missing header in canonical allowlist: {h}"
