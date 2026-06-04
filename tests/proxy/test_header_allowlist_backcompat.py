"""Back-compat guard for the v1.7.1 header-allowlist consolidation.

The canonical ``CLAUDE_CODE_HEADER_ALLOWLIST`` now lives in
``tokenpak.proxy.headers``. ``tokenpak.proxy.passthrough`` re-exports it as a
compatibility alias so the historical import path keeps working unchanged.
This test fails loudly if that alias is ever dropped.
"""


def test_passthrough_allowlist_backcompat_alias_resolves_to_canonical():
    # historical import path must still work
    from tokenpak.proxy.headers import CLAUDE_CODE_HEADER_ALLOWLIST as canonical
    from tokenpak.proxy.passthrough import CLAUDE_CODE_HEADER_ALLOWLIST as alias

    # and resolve to the exact canonical object (not a divergent copy)
    assert alias is canonical
    assert len(alias) > 0
