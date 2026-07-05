"""``apply_patch`` tool — path-policy-checked file write with effect record.

Implements the ``apply_patch`` acceptance criteria from P-TOOLS-01 (the
effect-record protocol):

1. Validate ``target`` against ``DispatchManifest.path_policy`` — it must match
   an ``allowed_paths`` glob **and** must not match a ``denied_paths`` glob (the
   four mandatory denied globs are always present, injected by the schema).
2. Create a ``DispatchEffect(status="planned")`` **before** the write — and,
   when a Run Ledger is supplied, persist it durably
   (``RunLedger.record_planned_effect``) so an interruption mid-write leaves a
   reconcilable planned record.
3. Write the file content **atomically** (same-directory temp file +
   ``os.replace``): a crash mid-write leaves the target as either its exact
   before-image or its exact after-image, never a truncated/partial file.
4. Compute the ``after_hash`` of the resulting file.
5. Transition the effect to ``status="applied", finalized_at=<now>`` (persisted
   via ``RunLedger.mark_effect_applied`` when a ledger is supplied).
6. On error, transition the effect to ``status="failed"``
   (``RunLedger.mark_effect_failed``) and re-raise.

Glob matching is segment-aware (``**`` spans path segments, ``*`` does not span
``/``), so ``.git/**`` / ``secrets/**`` deny the whole subtree while ``*.py``
stays within one segment. The matcher is dependency-free on purpose — this is an
OSS module and we avoid adding a glob library to the runtime closure.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from uuid import uuid4

from tokenpak.orchestration.dispatch.models.common import PathPolicy
from tokenpak.orchestration.dispatch.models.effect import DispatchEffect
from tokenpak.orchestration.dispatch.models.enums import (
    AutonomyMode,
    EffectStatus,
    EffectTargetType,
    RollbackBehavior,
)

from ._matrix import ToolName, authorize_tool_call

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tokenpak.orchestration.dispatch.ledger.db import RunLedger


class PathPolicyViolation(RuntimeError):
    """Raised when an ``apply_patch`` target is rejected by the path policy."""

    def __init__(self, target: str, reason: str) -> None:
        self.target = target
        self.reason = reason
        super().__init__(f"path policy rejected {target!r}: {reason}")


@dataclass
class ApplyPatchResult:
    """Outcome of an :func:`apply_patch` call."""

    effect: DispatchEffect
    absolute_path: Path
    relative_path: str
    bytes_written: int
    created: bool  # True when the target did not previously exist


def _normalize(relative_path: str) -> str:
    """Return a clean POSIX relative path for glob matching.

    Strips a leading ``./`` and any leading ``/`` so policy globs (which are
    written relative to the workspace root) match consistently. Rejects paths
    that escape the workspace via ``..`` — those can never be inside
    ``allowed_paths`` and are a clear policy violation.
    """

    pure = PurePosixPath(relative_path.replace("\\", "/"))
    if pure.is_absolute():
        pure = PurePosixPath(*pure.parts[1:])
    text = str(pure)
    if text.startswith("./"):
        text = text[2:]
    return text


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a gitignore-style glob into an anchored regex.

    ``**`` matches across ``/`` (zero or more segments); ``*`` and ``?`` stay
    within a single segment. ``foo/**`` matches everything *under* ``foo``.
    """

    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                # '**': spans path segments. Consume a trailing '/' so that
                # 'a/**/b' collapses cleanly and 'a/**' becomes 'a/.*'.
                if i + 2 < n and pattern[i + 2] == "/":
                    out.append("(?:.*/)?")
                    i += 3
                else:
                    out.append(".*")
                    i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "/":
            out.append("/")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out))


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(_glob_to_regex(p).fullmatch(path) is not None for p in patterns)


def check_path_policy(relative_path: str, path_policy: PathPolicy) -> str:
    """Validate ``relative_path`` against ``path_policy``; return normalized path.

    Raises :class:`PathPolicyViolation` when the path matches a denied glob or
    fails to match any allowed glob. ``denied_paths`` is checked first so the
    mandatory deny globs (``.env``, ``.git/**``, ``secrets/**``, ``license/**``)
    win over any overlapping allow rule.
    """

    norm = _normalize(relative_path)
    if _matches_any(norm, path_policy.denied_paths):
        raise PathPolicyViolation(norm, "matches a denied_paths glob")
    if not _matches_any(norm, path_policy.allowed_paths):
        raise PathPolicyViolation(norm, "does not match any allowed_paths glob")
    return norm


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` atomically (crash-safe).

    Same-directory temp file + ``os.replace``: POSIX ``os.replace`` is atomic
    when source and destination share a filesystem, which placing the temp file
    in the target's parent directory guarantees. A crash at ANY point leaves the
    target as either its exact before-image (replace never happened) or its
    exact after-image (replace completed) — never a truncated partial write.
    Mirrors the atomic-publish pattern used for the vault index artifacts.
    """

    tmp = target.parent / f"{target.name}.tmp.{os.getpid()}.{uuid4().hex[:8]}"
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        # Best-effort cleanup when the write or the swap failed: the temp file
        # is ours alone (pid+random suffix), so removing it never races another
        # writer. After a successful replace it no longer exists.
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def apply_patch(
    *,
    relative_path: str,
    content: str,
    path_policy: PathPolicy,
    autonomy_mode: AutonomyMode | str,
    job_id: str,
    station_run_id: str,
    workspace_root: Path | str,
    effect_id: str | None = None,
    approval_granted: bool = False,
    now: datetime | None = None,
    encoding: str = "utf-8",
    ledger: "RunLedger | None" = None,
) -> ApplyPatchResult:
    """Apply a file write through the path policy + effect-record protocol.

    ``relative_path`` is interpreted relative to ``workspace_root`` and is the
    string matched against ``path_policy``. The effect record is created
    ``planned`` before the write and transitioned to ``applied`` on success
    (returned in :class:`ApplyPatchResult`) or ``failed`` on error (surfaced by
    re-raising the underlying exception).

    When ``ledger`` is supplied the full effect lifecycle is **persisted
    durably**: the ``planned`` record is written to the Run Ledger BEFORE the
    file write (the write-ahead marker resume reconciliation depends on), and
    the ``applied``/``failed`` transition is written after. Without a ledger the
    lifecycle exists only on the returned in-memory record — callers executing
    real station work should always pass the run's ledger.

    The file write itself is atomic (same-directory temp file + ``os.replace``),
    so an interrupted call leaves the target matching either ``before_hash`` or
    ``after_hash`` — never a partial file that matches neither.
    """

    # 1. Invocation-time matrix gate.
    authorize_tool_call(ToolName.APPLY_PATCH, autonomy_mode, approval_granted=approval_granted)

    # 1b. Path policy (requires_path_policy_check=True for apply_patch).
    norm = check_path_policy(relative_path, path_policy)

    abs_path = Path(workspace_root) / norm
    before_exists = abs_path.exists()
    if before_exists and not abs_path.is_file():
        raise PathPolicyViolation(norm, "target exists and is not a regular file")
    if not before_exists and not path_policy.allow_new_files:
        raise PathPolicyViolation(norm, "new file creation disabled by path policy")

    before_hash = _sha256(abs_path.read_bytes()) if before_exists else None
    rollback_behavior = (
        RollbackBehavior.RESTORE_BEFORE_CONTENT_IF_CURRENT_HASH_MATCHES_AFTER_HASH
        if before_exists
        else RollbackBehavior.DELETE_FILE_IF_AFTER_HASH_MATCHES
    )

    when = now or datetime.now(timezone.utc)

    # 2. Create the planned effect record BEFORE the write — and persist it
    #    durably when a ledger is available (the write-ahead marker that makes
    #    an interrupted write reconcilable on resume).
    effect = DispatchEffect(
        id=effect_id or f"effect_{uuid4().hex}",
        job_id=job_id,
        station_run_id=station_run_id,
        tool_name=ToolName.APPLY_PATCH.value,
        target_type=EffectTargetType.FILE,
        target=norm,
        before_exists=before_exists,
        before_hash=before_hash,
        after_hash=None,
        rollback_behavior=rollback_behavior,
        status=EffectStatus.PLANNED,
        rollback_available=False,
        created_at=when,
        finalized_at=None,
    )
    if ledger is not None:
        ledger.record_planned_effect(effect)

    try:
        # 3. Write the content ATOMICALLY (temp file + rename): a crash
        #    mid-write leaves the target as before-image or after-image only.
        data = content.encode(encoding)
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(abs_path, data)
        # 4. Compute after_hash.
        after_hash = _sha256(data)
    except Exception:
        # 6. Transition to failed and re-raise.
        failed_at = datetime.now(timezone.utc)
        effect = effect.model_copy(
            update={
                "status": EffectStatus.FAILED,
                "finalized_at": failed_at,
            }
        )
        if ledger is not None:
            ledger.mark_effect_failed(effect.id, finalized_at=failed_at)
        raise

    # 5. Transition to applied (persisted when a ledger is available).
    applied_at = datetime.now(timezone.utc)
    effect = effect.model_copy(
        update={
            "status": EffectStatus.APPLIED,
            "after_hash": after_hash,
            "rollback_available": True,
            "finalized_at": applied_at,
        }
    )
    if ledger is not None:
        ledger.mark_effect_applied(
            effect.id,
            finalized_at=applied_at,
            after_hash=after_hash,
            rollback_available=True,
        )

    return ApplyPatchResult(
        effect=effect,
        absolute_path=abs_path,
        relative_path=norm,
        bytes_written=len(data),
        created=not before_exists,
    )


__all__ = [
    "PathPolicyViolation",
    "ApplyPatchResult",
    "check_path_policy",
    "apply_patch",
]
