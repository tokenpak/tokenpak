"""
TokenPak Compaction — Standard Compression Policies.

Public API::

    from tokenpak.compaction import (
        CompactionMode,
        CompactionPolicy,
        BlockPolicy,
        compact,
    )

    # One-shot compaction
    result = compact(text, mode="balanced", target_tokens=2000)

    # Policy-driven compaction
    policy = CompactionPolicy.from_dict({
        "compaction": {
            "mode": "balanced",
            "max_tokens": 8000,
            "priority_order": ["instructions", "code", "knowledge"],
            "per_block_limits": {
                "instructions": {"mode": "lossless"},
                "code": {"mode": "balanced", "max_tokens": 2000},
            },
        }
    })
    result = policy.compact_block(text, block_type="code")
"""

from .modes import CompactionMode, compact
from .policy import BlockPolicy, CompactionPolicy

__all__ = [
    "CompactionMode",
    "CompactionPolicy",
    "BlockPolicy",
    "compact",
]
