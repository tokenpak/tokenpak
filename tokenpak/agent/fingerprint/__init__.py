"""
TokenPak Fingerprint Module — structural analysis without content leakage.

Public API:
    FingerprintGenerator  — analyze prompt structure
    PrivacyLevel          — control detail level
    FingerprintSync       — send to intelligence server, receive directives
"""

from .generator import Fingerprint, FingerprintGenerator, Segment
from .privacy import PrivacyLevel, apply_privacy
from .sync import FingerprintSync, SyncResult

__all__ = [
    "FingerprintGenerator",
    "Fingerprint",
    "Segment",
    "PrivacyLevel",
    "apply_privacy",
    "FingerprintSync",
    "SyncResult",
]
