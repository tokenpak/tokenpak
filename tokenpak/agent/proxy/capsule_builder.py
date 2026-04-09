"""
tokenpak.agent.proxy.capsule_builder
=====================================

Proxy-layer access to the CapsuleBuilder.

This module exposes :class:`CapsuleBuilder` at the
``tokenpak.agent.proxy.capsule_builder`` import path that the proxy
pipeline expects, delegating to the canonical implementation in
``tokenpak.capsule.builder``.

Typical use
-----------
::

    from tokenpak.agent.proxy.capsule_builder import CapsuleBuilder

    builder = CapsuleBuilder(enabled=True)
    new_body, stats = builder.process(request_body_bytes)

Or via the feature-flag-aware factory:

::

    from tokenpak.agent.proxy.capsule_builder import make_capsule_builder

    builder = make_capsule_builder()  # respects TOKENPAK_CAPSULE_BUILDER env var
    new_body, stats = builder.process(request_body_bytes)
"""

from __future__ import annotations

import os

# Re-export the canonical implementation so callers can do:
#   from tokenpak.agent.proxy.capsule_builder import CapsuleBuilder
from tokenpak.capsule.builder import (
    DEFAULT_HOT_WINDOW,
    DEFAULT_MIN_BLOCK_CHARS,
    CapsuleBuilder,
)

__all__ = [
    "CapsuleBuilder",
    "DEFAULT_HOT_WINDOW",
    "DEFAULT_MIN_BLOCK_CHARS",
    "make_capsule_builder",
]


def make_capsule_builder(
    *,
    min_block_chars: int = DEFAULT_MIN_BLOCK_CHARS,
    hot_window: int = DEFAULT_HOT_WINDOW,
) -> CapsuleBuilder:
    """
    Factory that reads the ``TOKENPAK_CAPSULE_BUILDER`` env var to decide
    whether the builder is enabled, then returns a ready-to-use
    :class:`CapsuleBuilder`.

    Parameters
    ----------
    min_block_chars : int
        Minimum character length before a block is considered for
        compression (default: ``DEFAULT_MIN_BLOCK_CHARS``).
    hot_window : int
        Number of trailing messages to leave uncompressed
        (default: ``DEFAULT_HOT_WINDOW``).

    Returns
    -------
    CapsuleBuilder
        An enabled or disabled builder depending on the feature flag.
    """
    enabled = os.environ.get("TOKENPAK_CAPSULE_BUILDER", "0") == "1"
    return CapsuleBuilder(
        enabled=enabled,
        min_block_chars=min_block_chars,
        hot_window=hot_window,
    )
