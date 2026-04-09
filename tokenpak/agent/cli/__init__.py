"""
TokenPak Agent CLI

Entry point for the tokenpak command-line interface.
"""

import warnings as _warnings
_warnings.warn(
    "tokenpak.agent.cli is deprecated, use tokenpak.cli instead. "
    "This will be removed in v2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from .main import main

__all__ = ["main"]
