"""tokenpak.services.providers — shared provider-facing service modules.

This subpackage hosts service-level provider integrations that are
consumed by the proxy, companion, and other entrypoints. Per the
architecture standard's 18-subsystem layout, services/ is the shared
execution backbone; proxy/ is the canonical transport over it.

Current modules:

- ``usage_parser`` (per-provider) — normalize provider-reported usage
  objects (including reasoning-model usage) into the TIP-1.0
  reasoning-usage-v1 schema. Dispatched dynamically from provider
  profile / capability metadata. Provider names MUST NOT be hardcoded
  by consumers — go through ``get_usage_parser(provider)`` instead.
"""

from tokenpak.services.providers._registry import (
    UsageParser,
    get_usage_parser,
    list_registered_providers,
    register_parser,
)

__all__ = [
    "UsageParser",
    "get_usage_parser",
    "list_registered_providers",
    "register_parser",
]
