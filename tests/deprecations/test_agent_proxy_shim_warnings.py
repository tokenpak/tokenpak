"""P-AP-06 acceptance: every tokenpak.agent.proxy.* legacy shim emits a
DeprecationWarning pointing at its canonical tokenpak.proxy.* home.
"""

from __future__ import annotations

import importlib
import sys
import warnings

import pytest

TOP_LEVEL_SHIMS = [
    "tokenpak.agent.proxy",
    "tokenpak.agent.proxy.capsule_builder",
    "tokenpak.agent.proxy.capsule_integration",
    "tokenpak.agent.proxy.circuit_breaker",
    "tokenpak.agent.proxy.connection_pool",
    "tokenpak.agent.proxy.degradation",
    "tokenpak.agent.proxy.example_selector",
    "tokenpak.agent.proxy.failover",
    "tokenpak.agent.proxy.failover_engine",
    "tokenpak.agent.proxy.intent_policy",
    "tokenpak.agent.proxy.oauth",
    "tokenpak.agent.proxy.passthrough",
    "tokenpak.agent.proxy.prompt_builder",
    "tokenpak.agent.proxy.proxy",
    "tokenpak.agent.proxy.router",
    "tokenpak.agent.proxy.server",
    "tokenpak.agent.proxy.server_async",
    "tokenpak.agent.proxy.startup",
    "tokenpak.agent.proxy.stats",
    "tokenpak.agent.proxy.stats_api",
    "tokenpak.agent.proxy.streaming",
    "tokenpak.agent.proxy.tool_schema_registry",
    "tokenpak.agent.proxy.providers",
    "tokenpak.agent.proxy.providers.anthropic",
    "tokenpak.agent.proxy.providers.detector",
    "tokenpak.agent.proxy.providers.google",
    "tokenpak.agent.proxy.providers.openai",
    "tokenpak.agent.proxy.providers.stream_translator",
    "tokenpak.agent.proxy.providers.translator",
]


@pytest.mark.parametrize("module_name", TOP_LEVEL_SHIMS)
def test_shim_emits_deprecation_warning(module_name: str) -> None:
    for dotted in [module_name, *[k for k in sys.modules if k.startswith(module_name)]]:
        sys.modules.pop(dotted, None)

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        importlib.import_module(module_name)

    shim_warnings = [
        w
        for w in captured
        if issubclass(w.category, DeprecationWarning)
        and module_name in str(w.message)
        and "tokenpak.proxy" in str(w.message)
    ]
    assert shim_warnings, (
        f"expected DeprecationWarning for {module_name!r} "
        f"pointing at canonical tokenpak.proxy.*, got: "
        f"{[str(w.message) for w in captured]}"
    )


def test_canonical_symbol_identity_preserved() -> None:
    """Importing via the shim should yield the identical class object."""
    for dotted in list(sys.modules):
        if dotted.startswith(("tokenpak.agent.proxy", "tokenpak.proxy")):
            sys.modules.pop(dotted, None)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from tokenpak.agent.proxy.server import ProxyServer as ShimProxyServer
        from tokenpak.proxy.server import ProxyServer as CanonicalProxyServer

    assert ShimProxyServer is CanonicalProxyServer
