"""
TokenPak Platform Adapters

Detects which agent platform is making requests (OpenClaw, Claude CLI, or
generic) and exposes the appropriate compression/routing configuration.

Usage::

    from tokenpak.agent.adapters.registry import detect_platform

    adapter = detect_platform(request_headers, os.environ)
    config = adapter.get_config()
"""

import warnings as _warnings
_warnings.warn(
    "tokenpak.agent.adapters is deprecated, use tokenpak.adapters instead. "
    "This will be removed in v2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from .base import BaseAdapter
from .claude_cli import ClaudeCLIAdapter
from .generic import GenericAdapter
from .openclaw import OpenClawAdapter
from .registry import detect_platform

__all__ = [
    "BaseAdapter",
    "OpenClawAdapter",
    "ClaudeCLIAdapter",
    "GenericAdapter",
    "detect_platform",
]
