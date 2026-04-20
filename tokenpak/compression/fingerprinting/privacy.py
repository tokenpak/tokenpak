"""
TokenPak Fingerprint Privacy — control how much structural detail is shared.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class PrivacyLevel(str, Enum):
    MINIMAL = "minimal"  # segment counts + total length only
    STANDARD = "standard"  # segment types + rough lengths (default)
    FULL = "full"  # complete structural fingerprint


def apply_privacy(fingerprint_dict: dict[str, Any], level: PrivacyLevel) -> dict[str, Any]:
    """
    Strip or blur fingerprint fields according to the given privacy level.
    Returns a new dict safe to transmit.
    """
    if level == PrivacyLevel.FULL:
        return dict(fingerprint_dict)

    out = {
        "fingerprint_id": fingerprint_dict.get("fingerprint_id"),
        "schema_version": fingerprint_dict.get("schema_version", 1),
        "total_tokens": fingerprint_dict.get("total_tokens"),
        "segment_count": fingerprint_dict.get("segment_count"),
        "language": fingerprint_dict.get("language"),
    }

    if level == PrivacyLevel.STANDARD:
        # Include segment type distribution but no content hashes
        segments = fingerprint_dict.get("segments", [])
        type_counts: dict[str, int] = {}
        for seg in segments:
            t = seg.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1
        out["segment_type_distribution"] = type_counts
        out["avg_segment_tokens"] = fingerprint_dict.get("total_tokens", 0) // max(len(segments), 1)

    # MINIMAL: just counts/total already set above
    return {k: v for k, v in out.items() if v is not None}
