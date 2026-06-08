"""``run_command`` tool — category-gated subprocess with effect record.

Implements the ``run_command`` acceptance criteria from P-TOOLS-01 (Standards
Delta v0 §5.3 + §4.8):

1. Validate the command ``category`` against the ``allowed_categories``
   allowlist.
2. Reject when the category is in ``forbidden_categories``.
3. Create a ``DispatchEffect(status="planned")`` for any *mutating* command.
4. Execute via :mod:`subprocess` with the station-loop timeout.
5. Capture stdout/stderr; promote the effect to ``applied`` on completion.

A command that *runs to completion* — even with a non-zero exit code — is a
successful tool invocation: the non-zero status is result data, captured in
:class:`RunCommandResult.returncode`. A tool *failure* (the effect transitions
to ``failed``) is reserved for the cases where the command could not run to
completion: a timeout (returned with ``timed_out=True`` and captured partial
output) or an OS-level launch error (re-raised). The mutating-command effect is created
``planned`` before launch so an interrupted run leaves a ``planned`` record
without ``finalized_at`` for resume reconciliation (§5.5, handled in P-EXEC-01).
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from tokenpak.orchestration.dispatch.models.common import StationLoopPolicy
from tokenpak.orchestration.dispatch.models.effect import DispatchEffect
from tokenpak.orchestration.dispatch.models.enums import (
    AutonomyMode,
    EffectStatus,
    EffectTargetType,
    RollbackBehavior,
)

from ._matrix import (
    ALLOWED_COMMAND_CATEGORIES,
    CATEGORY_MUTATES_WORKSPACE,
    FORBIDDEN_COMMAND_CATEGORIES,
    CommandCategory,
    ToolName,
    authorize_tool_call,
)

# System-default station timeout (Standards Delta v0 §5.4 max_wall_seconds).
_DEFAULT_TIMEOUT_SECONDS = StationLoopPolicy().max_wall_seconds


class CommandCategoryError(ValueError):
    """Raised when a ``run_command`` category is forbidden or not on the allowlist."""

    def __init__(self, category: CommandCategory, reason: str) -> None:
        self.category = category
        self.reason = reason
        super().__init__(f"run_command category {category.value!r} rejected: {reason}")


@dataclass
class RunCommandResult:
    """Outcome of a :func:`run_command` call."""

    effect: DispatchEffect | None  # None for non-mutating (inspection) commands
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    command: list[str]


def _coerce_category(category: CommandCategory | str) -> CommandCategory:
    return category if isinstance(category, CommandCategory) else CommandCategory(category)


def validate_command_category(category: CommandCategory | str) -> CommandCategory:
    """Validate a command category against the §5.3 allow/forbid sets.

    Raises :class:`CommandCategoryError` when the category is explicitly
    forbidden or is simply not on the two-entry allowlist. Returns the coerced
    :class:`CommandCategory` when permitted.
    """

    cat = _coerce_category(category)
    if cat in FORBIDDEN_COMMAND_CATEGORIES:
        raise CommandCategoryError(cat, "category is in run_command.forbidden_categories")
    if cat not in ALLOWED_COMMAND_CATEGORIES:
        raise CommandCategoryError(cat, "category is not in run_command.allowed_categories")
    return cat


def run_command(
    *,
    command: Sequence[str],
    category: CommandCategory | str,
    autonomy_mode: AutonomyMode | str,
    job_id: str,
    station_run_id: str,
    cwd: Path | str | None = None,
    timeout_seconds: int | None = None,
    env: Mapping[str, str] | None = None,
    effect_id: str | None = None,
    approval_granted: bool = False,
    now: datetime | None = None,
) -> RunCommandResult:
    """Run an allowlisted command, recording a mutating effect when applicable.

    ``command`` is an argv list (no shell). ``timeout_seconds`` defaults to the
    §5.4 system default (``max_wall_seconds``). A non-mutating category
    (``read_only_inspection``) records no effect (``effect is None``); a mutating
    category (``tests``) records a ``command_output`` effect, ``planned`` before
    launch and ``applied`` after completion.
    """

    # 1. Matrix gate (Standards Delta v0 §5.3).
    authorize_tool_call(ToolName.RUN_COMMAND, autonomy_mode, approval_granted=approval_granted)

    # 1b. + 2. Category allowlist / forbidden-list enforcement.
    cat = validate_command_category(category)

    argv = list(command)
    timeout = _DEFAULT_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    mutating = CATEGORY_MUTATES_WORKSPACE[cat]
    when = now or datetime.now(timezone.utc)

    # 3. Create the planned effect for mutating commands only (§5.3).
    effect: DispatchEffect | None = None
    if mutating:
        effect = DispatchEffect(
            id=effect_id or f"effect_{uuid4().hex}",
            job_id=job_id,
            station_run_id=station_run_id,
            tool_name=ToolName.RUN_COMMAND.value,
            target_type=EffectTargetType.COMMAND_OUTPUT,
            target=" ".join(argv),
            before_exists=False,
            before_hash=None,
            after_hash=None,
            # Command effects are not auto-revertible by hash; recovery is manual.
            rollback_behavior=RollbackBehavior.MANUAL_ONLY,
            status=EffectStatus.PLANNED,
            rollback_available=False,
            created_at=when,
            finalized_at=None,
        )

    # 4. Execute.
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        if effect is not None:
            effect = effect.model_copy(
                update={
                    "status": EffectStatus.FAILED,
                    "finalized_at": datetime.now(timezone.utc),
                }
            )
        return RunCommandResult(
            effect=effect,
            returncode=-1,
            stdout=exc.stdout or "" if isinstance(exc.stdout, str) else "",
            stderr=exc.stderr or "" if isinstance(exc.stderr, str) else "",
            timed_out=True,
            command=argv,
        )
    except OSError:
        # Could not launch the process at all → failed effect, re-raise.
        if effect is not None:
            effect = effect.model_copy(
                update={
                    "status": EffectStatus.FAILED,
                    "finalized_at": datetime.now(timezone.utc),
                }
            )
        raise

    # 5. Promote the effect to applied (command ran to completion).
    if effect is not None:
        effect = effect.model_copy(
            update={
                "status": EffectStatus.APPLIED,
                "finalized_at": datetime.now(timezone.utc),
            }
        )

    return RunCommandResult(
        effect=effect,
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        timed_out=False,
        command=argv,
    )


__all__ = [
    "CommandCategoryError",
    "RunCommandResult",
    "validate_command_category",
    "run_command",
]
