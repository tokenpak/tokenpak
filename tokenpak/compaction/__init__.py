"""
TokenPak Compaction — Standard Compression Policies.

Public API::

    from tokenpak.compaction import (
        CompactionMode,
        CompactionPolicy,
        BlockPolicy,
        compact,
        TopicAwarePolicy,
        TopicBoundaryDetector,
        TopicSegment,
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

    # Topic-aware compaction
    topic_policy = TopicAwarePolicy(
        active_mode="balanced",
        inactive_mode="aggressive",
        activity_threshold=0.5,
    )
    result = topic_policy.compact_with_topics(text)
"""

from .modes import CompactionMode, compact
from .policy import BlockPolicy, CompactionPolicy, TopicAwarePolicy
from .topic_aware import TopicBoundaryDetector, TopicSegment

__all__ = [
    "CompactionMode",
    "CompactionPolicy",
    "BlockPolicy",
    "compact",
    "TopicAwarePolicy",
    "TopicBoundaryDetector",
    "TopicSegment",
]
