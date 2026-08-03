"""
tokenpak.proxy.capsule_builder
=====================================

Proxy-layer access to the CapsuleBuilder.

This module exposes :class:`CapsuleBuilder` at the
``tokenpak.proxy.capsule_builder`` import path that the proxy
pipeline expects, delegating to the canonical implementation in
``tokenpak.companion.capsules.builder``.

Typical use
-----------
::

    from tokenpak.proxy.capsule_builder import CapsuleBuilder

    builder = CapsuleBuilder(enabled=True)
    new_body, stats = builder.process(request_body_bytes)

Or via the feature-flag-aware factory:

::

    from tokenpak.proxy.capsule_builder import make_capsule_builder

    builder = make_capsule_builder()  # respects TOKENPAK_PAK_BUILDER env var
                                      # (legacy alias: TOKENPAK_CAPSULE_BUILDER)
    new_body, stats = builder.process(request_body_bytes)
"""

from __future__ import annotations

# Re-export the canonical implementation so callers can do:
#   from tokenpak.proxy.capsule_builder import CapsuleBuilder
from tokenpak.companion.capsules.builder import (
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
    Factory that reads the ``TOKENPAK_PAK_BUILDER`` env var (falling back
    to the legacy ``TOKENPAK_CAPSULE_BUILDER`` spelling) to decide whether
    the builder is enabled, then returns a ready-to-use
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
    from tokenpak.core.config_loader import _bool_env
    from tokenpak.proxy.config import env_or_profile as _env_or_profile

    # Canonical flag first; the pre-rebrand spelling stays honored so
    # existing configs keep working.
    flag = _env_or_profile("TOKENPAK_PAK_BUILDER", "")
    if not flag:
        flag = _env_or_profile("TOKENPAK_CAPSULE_BUILDER", "0")
    enabled = _bool_env(flag)
    return CapsuleBuilder(
        enabled=enabled,
        min_block_chars=min_block_chars,
        hot_window=hot_window,
    )
