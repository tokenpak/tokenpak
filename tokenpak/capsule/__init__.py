"""
tokenpak.capsule
================

Context-block capsule compression for the TokenPak proxy pipeline.

The capsule builder compresses verbose historical message blocks into compact,
structured capsules before the request is forwarded to the upstream model.

Feature flag: ``TOKENPAK_CAPSULE_BUILDER=1`` (env var) enables the builder.
"""

from .builder import CapsuleBuilder  # noqa: F401

__all__ = ["CapsuleBuilder"]
