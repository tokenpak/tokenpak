"""Deprecated re-export shim (TokenPak agent/cli consolidation 2026-04-20).

Canonical home: ``tokenpak.cli.commands.workflow``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.commands.workflow is a deprecated re-export; "
    "import from tokenpak.cli.commands.workflow instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.commands.workflow import *  # noqa: F401,F403,E402

__all__ = ["SEP", "WORKFLOW_TEMPLATES", "WorkflowStatus", "WorkflowStep", "cancel_cmd", "create_cmd", "datetime", "delete_cmd", "get_manager", "history_cmd", "list_cmd", "list_templates", "recover_cmd", "resume_cmd", "status_cmd", "template_steps", "templates_cmd", "workflow_cmd"]
