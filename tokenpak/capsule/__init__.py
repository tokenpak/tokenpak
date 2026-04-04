"""
tokenpak.capsule
================

Context-block capsule compression for the TokenPak proxy pipeline.

The capsule builder compresses verbose historical message blocks into compact,
structured capsules before the request is forwarded to the upstream model.

Feature flag: ``TOKENPAK_CAPSULE_BUILDER=1`` (env var) enables the builder.
"""

from .builder import (  # noqa: F401
    CapsuleBuilder,
    strip_capsule_tags,
    strip_capsule_tags_from_response,
)

__all__ = ["CapsuleBuilder", "strip_capsule_tags", "strip_capsule_tags_from_response"]
