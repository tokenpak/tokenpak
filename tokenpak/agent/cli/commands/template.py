"""Deprecated re-export shim (TokenPak agent/cli consolidation 2026-04-20).

Canonical home: ``tokenpak.cli.commands.template``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.commands.template is a deprecated re-export; "
    "import from tokenpak.cli.commands.template instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.commands.template import *  # noqa: F401,F403,E402

__all__ = ["template_cmd", "template_create", "template_delete", "template_list", "template_use"]
