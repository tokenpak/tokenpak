"""Compatibility checks for contracts moved from ``tokenpak.tip``."""

from importlib import import_module

import pytest

from tokenpak.core.contracts.cache import CacheMissReason
from tokenpak.services import optimization as services_optimization
from tokenpak.services.optimization import cache_trace

_CONTRACT_MODULES = (
    ("cache_contract", "cache"),
    ("capabilities", "capabilities"),
    ("compression_contract", "compression"),
    ("context_package", "context"),
    ("fidelity_contract", "fidelity"),
    ("optimization_contract", "optimization"),
    ("pak", "pak"),
    ("route_contract", "route"),
    ("telemetry_contract", "telemetry"),
    ("trace_contract", "trace"),
)


def test_cache_miss_reason_has_one_contract_home():
    assert services_optimization.CacheMissReason is CacheMissReason
    assert cache_trace.CacheMissReason is CacheMissReason


@pytest.mark.parametrize(("legacy_name", "canonical_name"), _CONTRACT_MODULES)
def test_legacy_contract_exports_are_canonical(legacy_name: str, canonical_name: str):
    legacy = import_module(f"tokenpak.tip.{legacy_name}")
    canonical = import_module(f"tokenpak.core.contracts.{canonical_name}")

    assert legacy.__all__ == canonical.__all__
    for export_name in canonical.__all__:
        assert getattr(legacy, export_name) is getattr(canonical, export_name)
