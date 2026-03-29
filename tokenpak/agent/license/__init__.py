"""TokenPak License System — key generation, validation, tier gating, seat counting.

Note: activation/store/validator have been moved to tokenpak.infrastructure.
This __init__ provides backward-compatible lazy re-exports to avoid circular
imports (license_validation → keys → this __init__ → license_activation cycle).
"""

from __future__ import annotations
from tokenpak.agent.license.keys import format_license_key, generate_keypair, sign_license, verify_license


def __getattr__(name: str):
    """Lazy imports — avoids circular import with tokenpak.infrastructure."""
    _lazy_modules = {
        "activation": "tokenpak.infrastructure.license_activation",
        "validator": "tokenpak.infrastructure.license_validation",
        "store": "tokenpak.infrastructure.license_store",
    }
    _lazy_attrs = {
        "activate": ("tokenpak.infrastructure.license_activation", "activate"),
        "deactivate": ("tokenpak.infrastructure.license_activation", "deactivate"),
        "get_plan": ("tokenpak.infrastructure.license_activation", "get_plan"),
        "get_license_key": ("tokenpak.infrastructure.license_activation", "get_license_key"),
        "is_enterprise": ("tokenpak.infrastructure.license_activation", "is_enterprise"),
        "is_pro": ("tokenpak.infrastructure.license_activation", "is_pro"),
        "is_team": ("tokenpak.infrastructure.license_activation", "is_team"),
        "LicenseStore": ("tokenpak.infrastructure.license_store", "LicenseStore"),
        "LicenseStatus": ("tokenpak.infrastructure.license_validation", "LicenseStatus"),
        "LicenseTier": ("tokenpak.infrastructure.license_validation", "LicenseTier"),
        "LicenseValidator": ("tokenpak.infrastructure.license_validation", "LicenseValidator"),
    }
    import importlib
    if name in _lazy_modules:
        return importlib.import_module(_lazy_modules[name])
    if name in _lazy_attrs:
        module_path, attr = _lazy_attrs[name]
        mod = importlib.import_module(module_path)
        return getattr(mod, attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "generate_keypair",
    "sign_license",
    "verify_license",
    "format_license_key",
    "LicenseValidator",
    "LicenseTier",
    "LicenseStatus",
    "LicenseStore",
    "activate",
    "deactivate",
    "get_plan",
    "is_pro",
    "is_team",
    "is_enterprise",
    "activation",
    "validator",
    "store",
]
