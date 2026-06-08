"""Dispatch tool registry, autonomy × tool matrix, and command categories.

Authoritative source: **Standards Delta v0 §5.3** (Tool Registry + autonomy ×
tool matrix). This module holds the *metadata and policy* layer that the two
effect-bearing tool implementations (``apply_patch``, ``run_command``) and the
station runner consume. The five tool descriptors and the 4×5 permission matrix
are transcribed verbatim from §5.3; do not add, drop, or re-grade a cell
without a Standards Delta amendment landing first.

Design split (kept deliberately impl-free so there is no import cycle):

* :data:`TOOL_REGISTRY` — name → :class:`ToolSpec` metadata for all five tools.
* :data:`AUTONOMY_TOOL_MATRIX` — ``AutonomyMode`` → ``ToolName`` →
  :class:`ToolPermission` grade.
* :func:`resolve_tool_permission` / :func:`authorize_tool_call` — the
  invocation-time gate (§5.3: "enforced at tool invocation time, NOT at
  result-time").
* Command-category allow/forbid sets for ``run_command`` (§5.3 ``run_command``
  block).

The tool *callables* (``apply_patch``, ``run_command``) live in sibling modules
and import from here; this module imports none of them, so the package has a
clean acyclic dependency graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from tokenpak.orchestration.dispatch.models.enums import AutonomyMode

# ``run_command.mutates_workspace`` is not a static bool — it depends on the
# command category (Standards Delta v0 §5.3). Sentinel kept as a module
# constant so the descriptor stays faithful to the spec text.
POLICY_DEPENDENT = "policy_dependent"


class ToolName(str, Enum):
    """The five v0.1-alpha Dispatch tools (Standards Delta v0 §5.3)."""

    READ_CONTEXT = "read_context"
    WRITE_ARTIFACT = "write_artifact"
    PROPOSE_PATCH = "propose_patch"
    APPLY_PATCH = "apply_patch"
    RUN_COMMAND = "run_command"


class ToolPermission(str, Enum):
    """Outcome grade for a single (autonomy_mode, tool) matrix cell.

    * ``ALLOWED`` — the ✓ cells; the tool may run unconditionally.
    * ``DENIED`` — the ✗ cells; invocation is rejected at call time.
    * ``APPROVAL`` — the tool may run only once an out-of-band approval has been
      granted (``dispatch_with_approval`` mode). The tool callable surfaces this
      via the ``approval_granted`` parameter; the approval *handshake* itself is
      the station runner / Gatehouse concern (P-EXEC-01), not this layer.
    * ``CONSTRAINED`` — the tool may run, but only inside the
      ``auto_dispatch_limited`` guard rails (path policy enforced; for
      ``run_command`` only the ``read_only_inspection`` + ``tests`` categories).
    """

    ALLOWED = "allowed"
    DENIED = "denied"
    APPROVAL = "approval"
    CONSTRAINED = "constrained"


class CommandCategory(str, Enum):
    """``run_command`` command categories (Standards Delta v0 §5.3).

    Two are on the allowlist; five are categorically forbidden. The split is
    encoded in :data:`ALLOWED_COMMAND_CATEGORIES` /
    :data:`FORBIDDEN_COMMAND_CATEGORIES`.
    """

    READ_ONLY_INSPECTION = "read_only_inspection"
    TESTS = "tests"
    INSTALL_DEPENDENCY = "install_dependency"
    DEPLOY = "deploy"
    MUTATE_SECRET = "mutate_secret"
    EXTERNAL_WRITE = "external_write"
    RELEASE_TAG = "release_tag"


# Standards Delta v0 §5.3 run_command.allowed_categories (verbatim).
ALLOWED_COMMAND_CATEGORIES: frozenset[CommandCategory] = frozenset(
    {CommandCategory.READ_ONLY_INSPECTION, CommandCategory.TESTS}
)

# Standards Delta v0 §5.3 run_command.forbidden_categories (verbatim).
FORBIDDEN_COMMAND_CATEGORIES: frozenset[CommandCategory] = frozenset(
    {
        CommandCategory.INSTALL_DEPENDENCY,
        CommandCategory.DEPLOY,
        CommandCategory.MUTATE_SECRET,
        CommandCategory.EXTERNAL_WRITE,
        CommandCategory.RELEASE_TAG,
    }
)

# Whether a permitted command category mutates the workspace. Only mutating
# commands require a DispatchEffect record (§5.3 run_command.requires_dispatch_
# effect: "for any mutating command"). ``tests`` may write fixtures / coverage
# artifacts, so it is treated as mutating; pure inspection is not.
CATEGORY_MUTATES_WORKSPACE: dict[CommandCategory, bool] = {
    CommandCategory.READ_ONLY_INSPECTION: False,
    CommandCategory.TESTS: True,
}


# ---------------------------------------------------------------------------
# Autonomy × tool matrix (Standards Delta v0 §5.3, transcribed verbatim)
# ---------------------------------------------------------------------------

_A = ToolPermission.ALLOWED
_X = ToolPermission.DENIED
_AP = ToolPermission.APPROVAL
_C = ToolPermission.CONSTRAINED

AUTONOMY_TOOL_MATRIX: dict[AutonomyMode, dict[ToolName, ToolPermission]] = {
    AutonomyMode.ADVISORY: {
        ToolName.READ_CONTEXT: _A,
        ToolName.WRITE_ARTIFACT: _A,
        ToolName.PROPOSE_PATCH: _X,
        ToolName.APPLY_PATCH: _X,
        ToolName.RUN_COMMAND: _X,
    },
    AutonomyMode.DRAFT: {
        ToolName.READ_CONTEXT: _A,
        ToolName.WRITE_ARTIFACT: _A,
        ToolName.PROPOSE_PATCH: _A,
        ToolName.APPLY_PATCH: _X,
        ToolName.RUN_COMMAND: _X,
    },
    AutonomyMode.DISPATCH_WITH_APPROVAL: {
        ToolName.READ_CONTEXT: _A,
        ToolName.WRITE_ARTIFACT: _A,
        ToolName.PROPOSE_PATCH: _A,
        ToolName.APPLY_PATCH: _AP,
        ToolName.RUN_COMMAND: _AP,
    },
    AutonomyMode.AUTO_DISPATCH_LIMITED: {
        ToolName.READ_CONTEXT: _A,
        ToolName.WRITE_ARTIFACT: _A,
        ToolName.PROPOSE_PATCH: _A,
        ToolName.APPLY_PATCH: _C,
        ToolName.RUN_COMMAND: _C,
    },
}


# ---------------------------------------------------------------------------
# Tool descriptors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    """Declared metadata for one Dispatch tool (Standards Delta v0 §5.3).

    ``mutates_workspace`` is ``True``/``False`` for the four file/artifact tools
    and the :data:`POLICY_DEPENDENT` sentinel for ``run_command``.
    ``allowed_autonomy_modes`` is the set of modes whose matrix cell is *not*
    ``DENIED`` — derived from :data:`AUTONOMY_TOOL_MATRIX` so the descriptor and
    the matrix can never drift.
    """

    name: ToolName
    mutates_workspace: bool | str
    requires_dispatch_effect: bool
    requires_path_policy_check: bool
    allowed_autonomy_modes: frozenset[AutonomyMode]


def _modes_allowing(tool: ToolName) -> frozenset[AutonomyMode]:
    """Modes whose matrix grade for ``tool`` is anything other than DENIED."""

    return frozenset(
        mode for mode, row in AUTONOMY_TOOL_MATRIX.items() if row[tool] is not ToolPermission.DENIED
    )


# Per-tool static flags (Standards Delta v0 §5.3 tool block). ``mutates`` /
# ``effect`` / ``path_check`` follow the YAML verbatim.
_TOOL_FLAGS: dict[ToolName, tuple[bool | str, bool, bool]] = {
    #                         mutates_workspace,  effect, path_check
    ToolName.READ_CONTEXT: (False, False, False),
    ToolName.WRITE_ARTIFACT: (False, False, False),
    ToolName.PROPOSE_PATCH: (False, False, False),
    ToolName.APPLY_PATCH: (True, True, True),
    ToolName.RUN_COMMAND: (POLICY_DEPENDENT, True, False),
}

TOOL_REGISTRY: dict[ToolName, ToolSpec] = {
    name: ToolSpec(
        name=name,
        mutates_workspace=mutates,
        requires_dispatch_effect=effect,
        requires_path_policy_check=path_check,
        allowed_autonomy_modes=_modes_allowing(name),
    )
    for name, (mutates, effect, path_check) in _TOOL_FLAGS.items()
}


# ---------------------------------------------------------------------------
# Errors + invocation-time gate
# ---------------------------------------------------------------------------


class ToolPolicyViolation(RuntimeError):
    """Raised when a tool is invoked in an autonomy mode that DENIES it (§5.3)."""

    def __init__(self, tool: ToolName, mode: AutonomyMode) -> None:
        self.tool = tool
        self.mode = mode
        super().__init__(
            f"tool {tool.value!r} is denied under autonomy mode {mode.value!r} "
            f"(Standards Delta v0 §5.3 autonomy × tool matrix)"
        )


class ApprovalRequiredError(RuntimeError):
    """Raised when an APPROVAL-graded cell is invoked without granted approval."""

    def __init__(self, tool: ToolName, mode: AutonomyMode) -> None:
        self.tool = tool
        self.mode = mode
        super().__init__(
            f"tool {tool.value!r} requires approval under autonomy mode "
            f"{mode.value!r}; invoke with approval_granted=True once the "
            f"approval handshake has resolved (Standards Delta v0 §5.3)"
        )


def _coerce_tool(tool: ToolName | str) -> ToolName:
    return tool if isinstance(tool, ToolName) else ToolName(tool)


def _coerce_mode(mode: AutonomyMode | str) -> AutonomyMode:
    return mode if isinstance(mode, AutonomyMode) else AutonomyMode(mode)


def resolve_tool_permission(tool: ToolName | str, mode: AutonomyMode | str) -> ToolPermission:
    """Return the matrix grade for ``(mode, tool)`` (Standards Delta v0 §5.3)."""

    return AUTONOMY_TOOL_MATRIX[_coerce_mode(mode)][_coerce_tool(tool)]


def authorize_tool_call(
    tool: ToolName | str,
    mode: AutonomyMode | str,
    *,
    approval_granted: bool = False,
) -> ToolPermission:
    """Invocation-time gate for one tool call (Standards Delta v0 §5.3).

    Returns the resolved :class:`ToolPermission` when the call may proceed.
    Raises :class:`ToolPolicyViolation` for a DENIED cell, and
    :class:`ApprovalRequiredError` for an APPROVAL cell that was invoked without
    ``approval_granted=True``. CONSTRAINED and ALLOWED cells return their grade;
    any *additional* constraints (path policy, command category) are enforced by
    the individual tool callables, not here.
    """

    tool_e = _coerce_tool(tool)
    mode_e = _coerce_mode(mode)
    permission = AUTONOMY_TOOL_MATRIX[mode_e][tool_e]
    if permission is ToolPermission.DENIED:
        raise ToolPolicyViolation(tool_e, mode_e)
    if permission is ToolPermission.APPROVAL and not approval_granted:
        raise ApprovalRequiredError(tool_e, mode_e)
    return permission


__all__ = [
    "POLICY_DEPENDENT",
    "ToolName",
    "ToolPermission",
    "CommandCategory",
    "ALLOWED_COMMAND_CATEGORIES",
    "FORBIDDEN_COMMAND_CATEGORIES",
    "CATEGORY_MUTATES_WORKSPACE",
    "AUTONOMY_TOOL_MATRIX",
    "ToolSpec",
    "TOOL_REGISTRY",
    "ToolPolicyViolation",
    "ApprovalRequiredError",
    "resolve_tool_permission",
    "authorize_tool_call",
]
