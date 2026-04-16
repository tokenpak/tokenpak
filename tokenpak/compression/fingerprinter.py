"""
Compression fingerprinter — re-exports public API from _internal.fingerprint.

Public surface:
    FingerPrinter        — alias for FingerprintGenerator (architecture-doc C2 name)
    FingerprintGenerator — structural prompt fingerprinter (no content leakage)
    Fingerprint          — fingerprint data class
    PrivacyLevel         — detail level control (MINIMAL / STANDARD / FULL)
    apply_privacy        — strip/blur fingerprint fields before transmission
    FingerprintSync      — send to intelligence server, receive directives
    SyncResult           — result of a FingerprintSync.sync() call
"""

from tokenpak.compression.fingerprinting.generator import Fingerprint, FingerprintGenerator
from tokenpak.compression.fingerprinting.privacy import PrivacyLevel, apply_privacy
from tokenpak.compression.fingerprinting.sync import FingerprintSync, SyncResult

# Public name used in ARCHITECTURE-RECOMMENDATION.md (C2: Content Fingerprinting)
FingerPrinter = FingerprintGenerator

__all__ = [
    "FingerPrinter",
    "FingerprintGenerator",
    "Fingerprint",
    "PrivacyLevel",
    "apply_privacy",
    "FingerprintSync",
    "SyncResult",
]
