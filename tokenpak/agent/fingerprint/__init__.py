"""
TokenPak Fingerprint Module — structural analysis without content leakage.

Public API:
    FingerprintGenerator  — analyze prompt structure
    PrivacyLevel          — control detail level
    FingerprintSync       — send to intelligence server, receive directives
"""

import warnings as _warnings
_warnings.warn(
    "tokenpak.agent.fingerprint is deprecated, use tokenpak._internal.fingerprint instead. "
    "This will be removed in v2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from .generator import Fingerprint, FingerprintGenerator, Segment
from .privacy import PrivacyLevel, apply_privacy
from .sync import FingerprintSync, SyncResult

__all__ = ['FingerprintGenerator', 'Fingerprint', 'Segment', 'PrivacyLevel', 'apply_privacy', 'FingerprintSync', 'SyncResult', 'generator', 'privacy', 'sync']
