"""
tokenpak.license — License tier enforcement.

Key symbols:
    LicenseTier        — OSS / PRO / TEAM / ENTERPRISE (IntEnum)
    TierRequiredError  — raised when a Pro+ feature is invoked on OSS
    requires_tier      — decorator factory for gating functions
    load_license       — call once at startup to load ~/.config/tokenpak/license.json
    get_active_tier    — return the currently loaded tier
"""
from tokenpak.license.tier import LicenseTier, TIER_FEATURES
from tokenpak.license.gates import TierRequiredError, requires_tier
from tokenpak.license.loader import get_active_tier, load_license

__all__ = [
    "LicenseTier",
    "TIER_FEATURES",
    "TierRequiredError",
    "requires_tier",
    "get_active_tier",
    "load_license",
]
