"""Dynamic per-provider usage-parser registry.

This module discovers usage parsers at import time by scanning sibling
modules in ``tokenpak.services.providers`` that expose:

    PROVIDER_NAME: str
    parse_usage(response_usage: dict) -> dict

The returned dict conforms to the TIP-1.0 reasoning-usage-v1 schema
(``schemas/tip/reasoning-usage-v1.json`` in the tokenpak/registry repo).

Provider names are NOT hardcoded by callers. Use ``get_usage_parser``
with a provider name resolved from provider profile / capability
metadata. Unknown providers return a no-op parser that emits
``usage_source='unavailable'``.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Iterable, Mapping
from typing import Literal

# A usage parser takes the provider's usage object (already extracted
# from the response body) and returns a dict conforming to
# reasoning-usage-v1.schema.json.
UsageRecord = dict[str, object]
UsageParser = Callable[[Mapping[str, object] | None], UsageRecord]
CostPolicy = Literal[
    "catalog_rate_estimate",
    "subscription_billed_unknown",
    "route_unknown",
]


_REGISTRY: dict[str, UsageParser] = {}
_INPUT_TOKENS_INCLUDE_CACHE: dict[str, bool] = {}
_COST_POLICIES: dict[str, CostPolicy] = {}


def register_parser(
    provider_name: str,
    parser: UsageParser,
    *,
    input_tokens_include_cache: bool | None = None,
    cost_policy: CostPolicy = "route_unknown",
) -> None:
    """Register a usage parser under a provider name.

    Idempotent: re-registering the same name overwrites silently. Callers
    that need a stricter semantic should check ``provider_name in
    list_registered_providers()`` first.
    """
    if not provider_name:
        raise ValueError("provider_name must be non-empty")
    if input_tokens_include_cache is not None and not isinstance(input_tokens_include_cache, bool):
        raise TypeError("input_tokens_include_cache must be bool or None")
    if cost_policy not in {
        "catalog_rate_estimate",
        "subscription_billed_unknown",
        "route_unknown",
    }:
        raise ValueError(f"unsupported cost policy: {cost_policy!r}")
    _REGISTRY[provider_name] = parser
    if input_tokens_include_cache is None:
        _INPUT_TOKENS_INCLUDE_CACHE.pop(provider_name, None)
    else:
        _INPUT_TOKENS_INCLUDE_CACHE[provider_name] = input_tokens_include_cache
    _COST_POLICIES[provider_name] = cost_policy


def list_registered_providers() -> Iterable[str]:
    return tuple(sorted(_REGISTRY))


def _unavailable_parser(_usage: Mapping[str, object] | None) -> UsageRecord:
    return {
        "input_tokens": None,
        "visible_output_tokens": None,
        "reasoning_tokens": None,
        "total_output_tokens": None,
        "total_billable_tokens": None,
        "reasoning_effort": None,
        "usage_source": "unavailable",
        "provider_usage_ref": None,
    }


def get_usage_parser(provider_name: str) -> UsageParser:
    """Return the parser for ``provider_name``, or a no-op parser.

    The no-op parser conforms to reasoning-usage-v1 but emits
    ``usage_source='unavailable'``. This keeps the downstream code path
    uniform — every request gets a reasoning-usage object, even when the
    provider is unrecognized.
    """
    return _REGISTRY.get(provider_name, _unavailable_parser)


def get_input_tokens_include_cache(provider_name: str) -> bool | None:
    """Return whether a provider's input count already includes cache classes.

    ``None`` means the provider did not declare the accounting semantic, so
    consumers must not substitute the provider input count into priced totals.
    Keeping this metadata beside parser registration preserves the closed
    reasoning-usage-v1 object while avoiding provider-name branches in callers.
    """
    return _INPUT_TOKENS_INCLUDE_CACHE.get(provider_name)


def get_cost_policy(provider_name: str) -> CostPolicy:
    """Return the provider-declared cost treatment.

    Unknown and dynamically added providers default to ``route_unknown`` so a
    parser registration alone can never turn a route-card estimate into an
    exact-spend claim.
    """
    return _COST_POLICIES.get(provider_name, "route_unknown")


def _discover() -> None:
    """Import sibling modules so they can self-register at module load.

    Modules under ``tokenpak.services.providers`` whose name does NOT
    start with ``_`` are imported. Each is expected to call
    ``register_parser(...)`` at import time. This is the dynamic
    discovery contract that keeps provider names out of consumer code.
    """
    package_name = __name__.rsplit(".", 1)[0]
    package = importlib.import_module(package_name)
    for module_info in pkgutil.iter_modules(package.__path__):
        name = module_info.name
        if name.startswith("_"):
            continue
        importlib.import_module(f"{package_name}.{name}")


_discover()
