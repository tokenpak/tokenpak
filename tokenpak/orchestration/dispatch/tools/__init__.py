"""TokenPak Dispatch tools — registry, autonomy matrix, and effect-bearing tools.

This package is the **Tool Registry** authored by P-TOOLS-01. It exposes the
five v0.1-alpha Dispatch tools and the policy layer that
governs them:

* :data:`TOOL_REGISTRY` — name → :class:`ToolSpec` for all five tools, each
  declaring ``mutates_workspace``, ``requires_dispatch_effect``,
  ``requires_path_policy_check`` and ``allowed_autonomy_modes``.
* :data:`AUTONOMY_TOOL_MATRIX` + :func:`authorize_tool_call` — the autonomy ×
  tool matrix, enforced **at invocation time** (not result-time).
* :func:`apply_patch` — path-policy-checked file write with DispatchEffect
  lifecycle (planned → applied/failed).
* :func:`run_command` — command-category-gated subprocess with DispatchEffect
  for mutating commands.

The three non-effect tools (``read_context``, ``write_artifact``,
``propose_patch``) are registered as descriptors here; their runtime behaviour
is non-mutating and carries no effect record, so P-TOOLS-01 ships their registry
metadata and the two effect-bearing implementations. Out of scope (per packet):
the DispatchEffect schema itself (P-SCHEMA-01), resume reconciliation and the
StationLoopPolicy runner (P-EXEC-01).
"""

from __future__ import annotations

from ._matrix import (
    ALLOWED_COMMAND_CATEGORIES,
    AUTONOMY_TOOL_MATRIX,
    CATEGORY_MUTATES_WORKSPACE,
    FORBIDDEN_COMMAND_CATEGORIES,
    POLICY_DEPENDENT,
    TOOL_REGISTRY,
    ApprovalRequiredError,
    CommandCategory,
    ToolName,
    ToolPermission,
    ToolPolicyViolation,
    ToolSpec,
    authorize_tool_call,
    resolve_tool_permission,
)
from .apply_patch import (
    ApplyPatchResult,
    PathPolicyViolation,
    apply_patch,
    check_path_policy,
)
from .run_command import (
    CommandCategoryError,
    RunCommandResult,
    run_command,
    validate_command_category,
)

__all__ = [
    # registry + matrix
    "TOOL_REGISTRY",
    "ToolSpec",
    "ToolName",
    "ToolPermission",
    "AUTONOMY_TOOL_MATRIX",
    "resolve_tool_permission",
    "authorize_tool_call",
    "ToolPolicyViolation",
    "ApprovalRequiredError",
    "POLICY_DEPENDENT",
    # command categories
    "CommandCategory",
    "ALLOWED_COMMAND_CATEGORIES",
    "FORBIDDEN_COMMAND_CATEGORIES",
    "CATEGORY_MUTATES_WORKSPACE",
    "CommandCategoryError",
    "validate_command_category",
    # apply_patch
    "apply_patch",
    "ApplyPatchResult",
    "PathPolicyViolation",
    "check_path_policy",
    # run_command
    "run_command",
    "RunCommandResult",
]
