"""TokenPak License System — key generation, validation, tier gating, seat counting.

NOTE: activation.py, store.py, and validator.py have been moved to
tokenpak.infrastructure.license_* as part of the clean architecture restructure.
This __init__.py re-exports from the new locations for backward compatibility.

Lazy imports are used here to avoid circular dependencies:
  infrastructure.license_validation → agent.license.keys (direct import)
  infrastructure.license_activation → infrastructure.license_validation
"""

import warnings as _warnings
_warnings.warn(
    "tokenpak.agent.license is deprecated, use tokenpak.infrastructure instead. "
    "This will be removed in v2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from .keys import format_license_key, generate_keypair, sign_license, verify_license


def __getattr__(name):
    """Lazy re-exports from infrastructure to break circular import."""
    _infra_map = {
        "activate": ("tokenpak.infrastructure.license_activation", "activate"),
        "deactivate": ("tokenpak.infrastructure.license_activation", "deactivate"),
        "get_plan": ("tokenpak.infrastructure.license_activation", "get_plan"),
        "is_enterprise": ("tokenpak.infrastructure.license_activation", "is_enterprise"),
        "is_pro": ("tokenpak.infrastructure.license_activation", "is_pro"),
        "is_team": ("tokenpak.infrastructure.license_activation", "is_team"),
        "LicenseStore": ("tokenpak.infrastructure.license_store", "LicenseStore"),
        "LicenseStatus": ("tokenpak.infrastructure.license_validation", "LicenseStatus"),
        "LicenseTier": ("tokenpak.infrastructure.license_validation", "LicenseTier"),
        "LicenseValidator": ("tokenpak.infrastructure.license_validation", "LicenseValidator"),
        "ValidationResult": ("tokenpak.infrastructure.license_validation", "ValidationResult"),
    }
    if name in _infra_map:
        import importlib
        mod_name, attr = _infra_map[name]
        mod = importlib.import_module(mod_name)
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
    "ValidationResult",
    "activate",
    "deactivate",
    "get_plan",
    "is_pro",
    "is_team",
    "is_enterprise",
]
