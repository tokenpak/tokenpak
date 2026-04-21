"""Deprecated re-export shim for ``tokenpak.agent.proxy.providers``.

The canonical home is ``tokenpak.proxy.providers``. This package
re-exports everything from the canonical home and will be
removed in TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.proxy.providers is a deprecated re-export; "
    "import from tokenpak.proxy.providers instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.proxy.providers import *  # noqa: F401,F403,E402

__all__ = ["AnthropicFormat", "GoogleFormat", "OpenAIFormat", "StreamingTranslator", "detect_provider", "translate_request", "translate_response"]
