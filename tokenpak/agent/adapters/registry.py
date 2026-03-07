"""
Adapter Registry — resolves the correct platform adapter for an incoming request.

Priority order (first match wins):
  1. OpenClawAdapter
  2. ClaudeCLIAdapter
  3. GenericAdapter  (always matches)
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Type

from .base import BaseAdapter
from .claude_cli import ClaudeCLIAdapter
from .generic import GenericAdapter
from .openclaw import OpenClawAdapter

# Ordered list of adapter classes — first match wins.
_ADAPTER_PRIORITY: List[Type[BaseAdapter]] = [
    OpenClawAdapter,
    ClaudeCLIAdapter,
    GenericAdapter,
]


def detect_platform(
    request_headers: Dict[str, str],
    env: Optional[Dict[str, str]] = None,
) -> BaseAdapter:
    """
    Detect the calling platform and return the matching adapter instance.

    Parameters
    ----------
    request_headers:
        HTTP request headers for the current request.
    env:
        Environment variable mapping.  Defaults to ``os.environ`` when None.

    Returns
    -------
    BaseAdapter
        The first adapter (in priority order) whose ``detect()`` returns True.
        Falls back to ``GenericAdapter`` — never returns None.
    """
    if env is None:
        env = dict(os.environ)

    for adapter_cls in _ADAPTER_PRIORITY:
        if adapter_cls.detect(request_headers, env):
            return adapter_cls()

    # Safety net — GenericAdapter.detect() always returns True, but just in case:
    return GenericAdapter()
