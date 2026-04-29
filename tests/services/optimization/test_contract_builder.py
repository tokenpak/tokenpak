"""build_contract eligibility surface."""

from __future__ import annotations

from types import SimpleNamespace

from tokenpak.services.optimization.contract_builder import (
    _LocalOptimizationContract,
    build_contract,
)


def test_empty_inputs_produce_empty_capability_set():
    contract = build_contract()
    # Either TIP-02 contract or local stub — both expose .has()
    assert contract.has("anything") is False


def test_adapter_with_capabilities_attr_propagates():
    adapter = SimpleNamespace(
        capabilities=frozenset({"tip.compression.v1", "tip.cache.proxy-managed"})
    )
    contract = build_contract(adapter=adapter)
    assert contract.has("tip.compression.v1") is True
    assert contract.has("tip.cache.proxy-managed") is True
    assert contract.has("tip.unknown") is False


def test_adapter_without_capabilities_attr_is_empty():
    adapter = SimpleNamespace()
    contract = build_contract(adapter=adapter)
    assert contract.has("any-capability") is False


def test_route_and_platform_propagate_to_local_stub():
    # Force the local stub by passing nothing TIP-02-shaped.
    contract = build_contract(
        platform="claude-code",
        route="claude-code",
        policy={"body": "byte_preserved"},
    )
    if isinstance(contract, _LocalOptimizationContract):
        assert contract.platform == "claude-code"
        assert contract.route_class == "claude-code"
        # Route policy is preserved in extras.
        assert contract.extras.get("policy", {}).get("body") == "byte_preserved"
    else:
        # If TIP-02 is importable in this workspace the contract just needs
        # to expose .has(); we can't introspect its private fields safely.
        assert hasattr(contract, "has")


def test_non_iterable_capabilities_returns_empty():
    adapter = SimpleNamespace(capabilities=42)  # int isn't iterable
    contract = build_contract(adapter=adapter)
    assert contract.has("tip.compression.v1") is False
