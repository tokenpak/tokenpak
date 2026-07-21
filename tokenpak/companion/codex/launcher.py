# SPDX-License-Identifier: Apache-2.0
"""Launcher for ``tokenpak codex`` — thin bootstrap for Codex with companion.

Selects and safely provisions a Codex home, installs the companion into that
home, then supervises the Codex child so the validated ``codex.pid`` lifecycle
sentinel can always be removed after a normal exit. ``--install-only`` performs
the same selected-home safety preflight and setup without spawning Codex.

Before any selected-home mutation it inspects Codex's local SQLite files using
read-only kernel metadata. A home attached to another live or suspended Codex
process surfaces an actionable wait/retry instead of a raw lock failure.

Companion features work without the launcher if the user manually
configures MCP, hooks, and AGENTS.md — the launcher is convenience.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING as _TYPE_CHECKING
from typing import Callable as _Callable

from ..config import CompanionConfig
from .accounting import (
    build_receipt,
    empty_usage,
    merge_usage,
    usage_from_json_line,
    utc_now,
    write_receipt,
)

if _TYPE_CHECKING:
    from .session_home import SessionPaths

_TEAL = "\033[38;2;0;180;170m"
_DIM = "\033[2m"
_RESET = "\033[0m"
_CLEAR_LINE = "\033[2K"
_TOKENPAK_CHATGPT_BASE_URL = "http://127.0.0.1:8766/v1"

_BYPASS_FLAG = "--dangerously-bypass-approvals-and-sandbox"
_BYPASS_ENV_VAR = "TOKENPAK_CODEX_BYPASS_APPROVALS_AND_SANDBOX"
_TRUTHY = {"1", "true", "yes"}
_STORAGE_PRESSURE_ERRNOS = {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}
_APPROVAL_ARGS = ("--ask-for-approval", "never")
_SANDBOX_ARGS = ("--sandbox", "danger-full-access")


def _bypass_env_enabled(env: dict[str, str] | None = None) -> bool:
    """Return True if the bypass env var is set to a truthy value (case-insensitive)."""
    src = env if env is not None else os.environ
    raw = src.get(_BYPASS_ENV_VAR, "")
    return raw.strip().lower() in _TRUTHY


def _launcher_mode_state() -> tuple[str, str | None]:
    """Return the fail-closed Codex launcher default and any state warning."""
    try:
        from tokenpak.cli.commands.permissions import _get_launcher_mode_status

        return _get_launcher_mode_status("codex")
    except Exception as exc:
        return "inherit", f"could not read launcher permission state ({type(exc).__name__})"


def _fleet_state_enabled() -> bool:
    """Compatibility helper: true when Codex resolves to full-bypass.

    The former global full-bypass boolean is now a compatibility alias for the
    per-client full-bypass launcher default. It remains launcher-scoped and
    never persists into ~/.codex/config.toml.
    """
    try:
        return _launcher_mode_state()[0] == "full-bypass"
    except Exception:
        return False


def _maybe_inject_bypass_flag(
    args: list[str], env: dict[str, str] | None = None, fleet: bool = False
) -> list[str]:
    """Return a new arg list with the Codex bypass flag injected when opted in.

    Two opt-in surfaces, both launcher-scoped:

    - ``fleet=True`` — TokenPak launcher fleet mode (canonical path; the
      caller reads it from TokenPak-owned state via
      :func:`_fleet_state_enabled`).
    - the env var ``TOKENPAK_CODEX_BYPASS_APPROVALS_AND_SANDBOX``
      (accepts ``1`` / ``true`` / ``yes``) — the Codex-side back-compat
      alias of fleet mode, kept for automation scripts that predate the
      permission-tier system. Same effect, same banner.

    The flag is a no-op if the user already passed it on the command line
    (no duplication). Never mutates the input list.
    """
    if not (fleet or _bypass_env_enabled(env)):
        return list(args)
    if _BYPASS_FLAG in args:
        return list(args)
    return [_BYPASS_FLAG, *args]


def _fleet_banner(env: dict[str, str] | None = None, fleet: bool = False) -> str | None:
    """Mandatory stderr banner text for fleet-mode launches (None when off).

    Canonical user-visible guardrail — do not remove or soften it.
    """
    if fleet or _bypass_env_enabled(env):
        return f"tokenpak: fleet mode — bypass flags injected ({_BYPASS_FLAG})"
    return None


def _has_option(args: list[str], long_name: str, short_name: str) -> bool:
    """Return true when argv contains either spelling of an option."""
    return any(
        arg in {long_name, short_name}
        or arg.startswith(f"{long_name}=")
        or arg.startswith(f"{short_name}=")
        for arg in args
    )


def _config_permission_overrides(args: list[str]) -> tuple[bool, bool]:
    """Return approval/sandbox axes explicitly set through ``-c/--config``."""
    values: list[str] = []
    for index, arg in enumerate(args):
        if arg in {"-c", "--config"}:
            if index + 1 < len(args):
                values.append(args[index + 1])
            continue
        for prefix in ("-c=", "--config="):
            if arg.startswith(prefix):
                values.append(arg[len(prefix) :])
                break

    approval = False
    sandbox = False
    for value in values:
        key = value.partition("=")[0].strip().strip("\"'")
        leaf = key.rsplit(".", 1)[-1]
        if leaf == "approval_policy" or key.startswith("approval_policy."):
            approval = True
        elif leaf == "sandbox_mode":
            sandbox = True
        elif leaf == "default_permissions":
            approval = True
            sandbox = True
    return approval, sandbox


def _apply_launcher_mode(
    args: list[str],
    mode: str,
    env: dict[str, str] | None = None,
) -> tuple[list[str], tuple[str, ...], str | None, str]:
    """Apply a stored launcher default without overriding explicit argv.

    Returns ``(argv, resolved_flags, skip_reason, effective_mode)``. The
    legacy environment variable remains an explicit full-bypass override.
    """
    out = list(args)
    effective_mode = "full-bypass" if _bypass_env_enabled(env) else mode
    if effective_mode not in {
        "inherit",
        "approval-bypass",
        "sandbox-bypass",
        "full-bypass",
    }:
        effective_mode = "inherit"
    if effective_mode == "inherit":
        return out, (), None, effective_mode

    explicit_combined = (
        _BYPASS_FLAG if _BYPASS_FLAG in out else "--yolo" if "--yolo" in out else None
    )
    has_combined = explicit_combined is not None
    config_approval, config_sandbox = _config_permission_overrides(out)
    has_approval = _has_option(out, "--ask-for-approval", "-a") or config_approval
    has_sandbox = _has_option(out, "--sandbox", "-s") or config_sandbox

    if effective_mode == "full-bypass":
        if has_combined:
            return out, (explicit_combined,), None, effective_mode
        if has_approval or has_sandbox:
            return (
                out,
                (),
                "explicit approval or sandbox arguments take precedence",
                effective_mode,
            )
        return [_BYPASS_FLAG, *out], (_BYPASS_FLAG,), None, effective_mode

    if has_combined:
        return (
            out,
            (),
            "an explicit full-bypass argument takes precedence",
            effective_mode,
        )
    if effective_mode == "approval-bypass":
        if has_approval:
            return out, (), "an explicit approval argument takes precedence", effective_mode
        return [*_APPROVAL_ARGS, *out], _APPROVAL_ARGS, None, effective_mode
    if has_sandbox:
        return out, (), "an explicit sandbox argument takes precedence", effective_mode
    return [*_SANDBOX_ARGS, *out], _SANDBOX_ARGS, None, effective_mode


def _launcher_mode_banner(
    mode: str,
    flags: tuple[str, ...],
    skip_reason: str | None,
) -> str | None:
    """Build the mandatory launch-time warning for a non-inherit mode."""
    if mode == "inherit":
        return None
    reset = "tokenpak permissions launcher inherit --client codex"
    if skip_reason:
        return (
            f"tokenpak WARNING: codex launcher default {mode} skipped: {skip_reason}. "
            f"Reset: `{reset}`."
        )
    risk = {
        "approval-bypass": (
            "approval prompts are disabled; the configured sandbox still applies "
            "(danger-full-access would make this effectively full bypass)"
        ),
        "sandbox-bypass": (
            "the sandbox is disabled; approval policy still applies "
            "(approval_policy=never would make this effectively full bypass)"
        ),
        "full-bypass": "approval prompts and the local sandbox are disabled",
    }[mode]
    rendered = " ".join(flags)
    return (
        f"tokenpak WARNING: codex launcher mode {mode} active; arguments: {rendered}; "
        f"{risk}. Use only in a trusted, externally isolated environment. "
        "Managed policy may still constrain or reject this launch. "
        f"Reset: `{reset}`."
    )


# ── Codex local-database lock preflight ─────────────────────────────
# Bounded total wait for a *live* holder to release before we give up and
# print remediation. A stopped/suspended holder never releases, so it is
# short-circuited without consuming this budget.
_LOCK_WAIT_TIMEOUT_S = 30.0
# How often we re-probe the databases while waiting.
_LOCK_POLL_INTERVAL_S = 0.5
_ESC = "\x1b"


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _drain_esc_pressed() -> bool:
    """Best-effort, non-blocking check for a pending Esc keypress.

    Returns True if Esc (``0x1b``) is waiting on stdin.  Never blocks and
    never raises: on any platform/terminal where raw keys are unreadable
    we report "not pressed" and rely on the bounded timeout instead.
    """
    try:
        import select
        import termios
        import tty
    except ImportError:  # non-POSIX — try the Windows console API.
        try:
            import msvcrt
        except ImportError:  # pragma: no cover - no raw-key source
            return False
        pressed = False
        while msvcrt.kbhit():  # pragma: no cover - needs a Windows console
            if msvcrt.getwch() == _ESC:
                pressed = True
        return pressed

    if not _stdin_is_tty():
        return False
    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except termios.error:
        return False
    try:
        tty.setcbreak(fd)
        pressed = False
        while select.select([sys.stdin], [], [], 0)[0]:
            ch = sys.stdin.read(1)
            if not ch:
                break
            if ch == _ESC:
                pressed = True
        return pressed
    finally:
        with contextlib.suppress(termios.error):
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _preflight_state_lock(
    *,
    home: Path | str | None = None,
    prober=None,
    interactive: bool | None = None,
    timeout_s: float = _LOCK_WAIT_TIMEOUT_S,
    poll_interval_s: float = _LOCK_POLL_INTERVAL_S,
    esc_pressed=None,
    sleep=None,
    monotonic=None,
    deadline: float | None = None,
) -> "int | None":
    """Preflight Codex-owned SQLite databases before exec.

    Returns ``None`` when it is safe to launch — the home is clear, or a
    contended lock cleared within the bounded wait.  Returns an ``int``
    exit code when the caller should abort instead of exec-ing into a
    contended home:

    * a suspended/stopped holder (which never releases) short-circuits to
      direct remediation without waiting;
    * a live holder is waited on up to ``timeout_s`` — in a TTY the user
      may press Esc to cancel promptly, non-interactive callers get
      concise retry lines and a bounded timeout;
    * either way, exhausting the wait prints the same actionable
      remediation guidance rather than letting Codex fail on a raw lock.

    The seams (``prober``/``esc_pressed``/``sleep``/``monotonic``/
    ``interactive``) are injectable so the wait loop is testable without a
    real TTY, real clock, or real key input.
    """
    from . import state_lock

    esc_pressed = esc_pressed or _drain_esc_pressed
    sleep = sleep or time.sleep
    monotonic = monotonic or time.monotonic
    deadline = deadline if deadline is not None else monotonic() + timeout_s
    if interactive is None:
        interactive = _stdin_is_tty()

    def invoke_probe():
        remaining = deadline - monotonic()
        if remaining <= 0:
            return state_lock.LockStatus(
                home=Path(home or os.environ.get("CODEX_HOME") or Path.home() / ".codex"),
                db_path=Path(home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
                / state_lock.STATE_DB_NAME,
                exists=True,
                locked=True,
                detail="holder inspection wall-time limit is incomplete; refusing unsafe access",
                diagnostics_complete=False,
                incomplete_reasons=["probe_timeout"],
            )

        results: "queue.SimpleQueue[tuple[bool, object]]" = queue.SimpleQueue()

        def run_probe() -> None:
            try:
                value = (
                    state_lock.probe(home, deadline=deadline, clock=monotonic)
                    if prober is None
                    else prober()
                )
                results.put((True, value))
            except BaseException as exc:  # fail closed; surfaced below
                results.put((False, exc))

        worker = threading.Thread(
            target=run_probe,
            name="tokenpak-codex-lock-probe",
            daemon=True,
        )
        worker.start()
        worker.join(timeout=max(0.0, remaining))
        if worker.is_alive():
            return state_lock.LockStatus(
                home=Path(home or os.environ.get("CODEX_HOME") or Path.home() / ".codex"),
                db_path=Path(home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
                / state_lock.STATE_DB_NAME,
                exists=True,
                locked=True,
                detail="holder inspection wall-time limit is incomplete; refusing unsafe access",
                diagnostics_complete=False,
                incomplete_reasons=["probe_timeout"],
            )
        ok, value = results.get()
        if ok:
            return value
        return state_lock.LockStatus(
            home=Path(home or os.environ.get("CODEX_HOME") or Path.home() / ".codex"),
            db_path=Path(home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
            / state_lock.STATE_DB_NAME,
            exists=True,
            locked=True,
            detail=(f"holder inspection raised {value.__class__.__name__}; refusing unsafe access"),
            diagnostics_complete=False,
            incomplete_reasons=["proc_inspection_incomplete"],
        )

    status = invoke_probe()
    if not status.locked:
        return None
    if not getattr(status, "diagnostics_complete", True):
        print(state_lock.remediation_hint(status), file=sys.stderr)
        return 1

    # A stopped/suspended holder never releases the lock — waiting is
    # futile, so surface direct remediation immediately.
    if status.stopped_pids:
        print(state_lock.remediation_hint(status), file=sys.stderr)
        return 1

    if interactive:
        print(
            "tokenpak: SQLite database is busy. Waiting to connect... Press Esc to cancel.",
            file=sys.stderr,
        )
    else:
        print(
            "tokenpak: Codex local database is busy; waiting up to "
            f"{int(timeout_s)}s for the holder to release "
            "(use TOKENPAK_CODEX_SESSION_MODE=isolated for a fresh home)...",
            file=sys.stderr,
        )

    while monotonic() < deadline:
        if interactive and esc_pressed():
            print(
                "tokenpak: cancelled while waiting for the Codex database lock.",
                file=sys.stderr,
            )
            print(state_lock.remediation_hint(status), file=sys.stderr)
            return 130
        sleep(min(poll_interval_s, max(0.0, deadline - monotonic())))
        if monotonic() >= deadline:
            break
        status = invoke_probe()
        if not status.locked:
            return None
        if not getattr(status, "diagnostics_complete", True):
            print(state_lock.remediation_hint(status), file=sys.stderr)
            return 1
        if status.stopped_pids:
            print(state_lock.remediation_hint(status), file=sys.stderr)
            return 1
        if not interactive:
            print(
                "tokenpak: still waiting for the Codex database lock...",
                file=sys.stderr,
            )

    print(
        f"tokenpak: Codex database still locked after {int(timeout_s)}s.",
        file=sys.stderr,
    )
    print(state_lock.remediation_hint(status), file=sys.stderr)
    return 1


def _run_codex_process(
    codex_args: list[str],
    env: dict[str, str],
    *,
    on_start: _Callable[[int], None] | None = None,
) -> tuple[int, dict[str, int | None]]:
    """Supervise Codex, optionally teeing JSONL and extracting usage.

    The launcher never signals or terminates the child.  Terminal-generated
    interrupts reach both foreground processes naturally; the parent keeps
    waiting until Codex exits so lifecycle cleanup cannot race a live child.
    """
    usage = empty_usage()
    json_mode = "--json" in codex_args
    proc = subprocess.Popen(
        codex_args,
        env=env,
        stdout=subprocess.PIPE if json_mode else None,
        stderr=None,
        text=True,
        bufsize=1,
    )
    # The terminal delivers Ctrl-C to the whole foreground process group.
    # Codex may consume SIGINT as an operation cancel and continue running;
    # the supervisory parent therefore ignores SIGINT after the child has
    # inherited the caller's original disposition, then trusts the child's
    # eventual exit status.  This also prevents a PIPE-drain interruption
    # from deadlocking JSON mode.
    previous_sigint = None
    return_code: int | None = None
    start_error: BaseException | None = None
    try:
        try:
            previous_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
        except (AttributeError, OSError, ValueError):
            previous_sigint = None

        if on_start is not None:
            try:
                on_start(proc.pid)
            except BaseException as exc:
                # A very fast command can exit before /proc identity transfer.
                # In that case the parent-owned lease remains valid until this
                # function returns, and the real child result wins.
                poll = getattr(proc, "poll", lambda: None)
                if poll() is None:
                    start_error = exc
                    print(
                        "tokenpak: PID sentinel transfer failed "
                        f"({exc}); continuing supervised launch",
                        file=sys.stderr,
                    )

        if json_mode:
            assert proc.stdout is not None
            forward_output = True
            while True:
                try:
                    line = proc.stdout.readline()
                except KeyboardInterrupt:
                    continue
                except BaseException:
                    # Closing our read end does not terminate the child.  It
                    # merely gives a still-writing child normal pipe-closure
                    # semantics; the finally block below still waits/reaps.
                    with contextlib.suppress(Exception):
                        proc.stdout.close()
                    break
                if not line:
                    break
                if forward_output:
                    try:
                        sys.stdout.write(line)
                        sys.stdout.flush()
                    except (BrokenPipeError, OSError, UnicodeError, KeyboardInterrupt):
                        # Continue draining so a downstream `head` cannot
                        # strand Codex behind a full PIPE while the lifecycle
                        # lease is released.
                        forward_output = False
                try:
                    usage = merge_usage(usage, usage_from_json_line(line))
                except (ValueError, TypeError, json.JSONDecodeError):
                    pass

        while True:
            try:
                return_code = proc.wait()
                break
            except KeyboardInterrupt:
                continue
    finally:
        # Every path after a successful Popen reaps the child before the
        # caller's `with lease` can remove codex.pid.
        if return_code is None:
            while True:
                try:
                    return_code = proc.wait()
                    break
                except KeyboardInterrupt:
                    continue
        if previous_sigint is not None:
            with contextlib.suppress(OSError, ValueError):
                signal.signal(signal.SIGINT, previous_sigint)
    if start_error is not None:
        raise start_error
    assert return_code is not None
    if return_code < 0:
        return 128 + abs(return_code), usage
    return return_code, usage


def _print_session_paths(paths: "SessionPaths") -> None:
    """Print the complete selected-home routing map at startup."""
    print("tokenpak: Codex session paths", file=sys.stderr)
    for label, value in paths.report_rows():
        print(f"  {label}: {value}", file=sys.stderr)


def _is_storage_pressure(exc: BaseException) -> bool:
    """Recognize nested ENOSPC/EDQUOT without retrying unrelated failures."""
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, OSError) and current.errno in _STORAGE_PRESSURE_ERRNOS:
            return True
        for linked in (current.__cause__, current.__context__):
            if isinstance(linked, BaseException):
                pending.append(linked)
    return False


def _run_isolated_retention(
    session_home,
    paths: "SessionPaths",
    *,
    phase: str,
    preserve_home: Path | None,
    remove_all_orphans: bool = False,
) -> object | None:
    """Run the receipt-governed engine without masking launch results."""
    tokenpak_home = session_home._generated_tokenpak_root(paths.home)
    try:
        cleanup = session_home.cleanup_isolated_homes(
            tokenpak_home,
            preserve_home=preserve_home,
            remove_all_orphans=remove_all_orphans,
            orphan_cleanup_reason="storage-pressure" if remove_all_orphans else phase,
        )
    except Exception as exc:
        print(
            f"tokenpak: isolated-home retention {phase} preserved all homes ({exc})",
            file=sys.stderr,
        )
        return None
    if cleanup.removed:
        print(
            f"tokenpak: isolated-home retention {phase} removed "
            f"{len(cleanup.removed)} orphan(s)",
            file=sys.stderr,
        )
    if cleanup.errors:
        print(
            f"tokenpak: isolated-home retention {phase} preserved uncertain home(s): "
            + "; ".join(cleanup.errors),
            file=sys.stderr,
        )
    return cleanup


@contextlib.contextmanager
def _lease_with_post_retention(lease, session_home, paths: "SessionPaths"):
    """Release the exact lease before the final isolated-home sweep."""
    try:
        yield lease
    finally:
        try:
            lease.release()
        except Exception as exc:
            # A failed exact-owner unlink leaves the sentinel/artifact in
            # place, which retention treats as protected.  Do not replace an
            # already-known child result with a cleanup-only exception.
            print(
                f"tokenpak: PID sentinel cleanup preserved for inspection ({exc})",
                file=sys.stderr,
            )
        if paths.mode == session_home.MODE_ISOLATED:
            _run_isolated_retention(
                session_home,
                paths,
                phase="post-session",
                preserve_home=None,
            )


def _vanilla_receipt_env() -> dict[str, str]:
    """Return a child environment with TokenPak companion state stripped."""
    return {key: value for key, value in os.environ.items() if not key.startswith("TOKENPAK_")}


def _receipt_only_setup_metadata() -> dict[str, object]:
    return {
        "mode": "receipt_only",
        "setup_completed": False,
        "receipt_wrapper_active": True,
        "tokenpak_mechanism_active": False,
        "profile": None,
        "budget_daily_usd": None,
        "rates_snapshot_refreshed": False,
        "mcp_registered": False,
        "hooks_enabled": False,
        "hooks_installed": False,
        "agents_md_installed": False,
        "skills_installed_count": 0,
    }


def _write_accounting_receipt(
    *,
    receipt_out: str,
    run_id: str,
    codex_args: list[str],
    setup: dict[str, object],
    started_at: str,
    start_monotonic: float,
    exit_code: int,
    status: str,
    usage: dict[str, int | None] | None = None,
    missing_evidence: list[str] | None = None,
) -> None:
    ended_at = utc_now()
    duration_ms = max(0, round((time.monotonic() - start_monotonic) * 1000))
    receipt = build_receipt(
        run_id=run_id,
        codex_args=codex_args,
        cwd=os.getcwd(),
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=duration_ms,
        exit_code=exit_code,
        status=status,
        setup=setup,
        usage=usage,
        missing_evidence=missing_evidence,
    )
    write_receipt(receipt_out, receipt)
    print(f"tokenpak: accounting receipt written ({receipt_out})", file=sys.stderr)


def main(
    args: list[str] | None = None,
    *,
    receipt_out: str | None = None,
    run_id: str | None = None,
) -> int:
    """Entry point for ``tokenpak codex``."""
    args = list(args if args is not None else sys.argv[1:])

    install_only = "--install-only" in args
    receipt_only = "--receipt-only" in args
    args = [a for a in args if a not in {"--install-only", "--receipt-only"}]

    if receipt_only and not (receipt_out and run_id):
        print(
            "tokenpak: --receipt-only requires --receipt-out and --run-id",
            file=sys.stderr,
        )
        return 2
    if receipt_only and install_only:
        print(
            "tokenpak: --receipt-only cannot be combined with --install-only",
            file=sys.stderr,
        )
        return 2

    # Resolve and expose every path before any selected-home write.  Unknown
    # modes fail closed; a typo must never fall back to shared state.
    from . import session_home

    try:
        paths = session_home.select_paths(workspace_dir=Path.cwd())
    except (session_home.InvalidSessionMode, ValueError) as exc:
        print(f"tokenpak: {exc}", file=sys.stderr)
        return 2
    _print_session_paths(paths)

    # This sweep is deliberately before preflight, lease acquisition, and
    # selected-home creation.  It therefore remains reachable after switching
    # to shared/workspace mode and can recover receipt-proven quarantines even
    # when the selected launch later blocks or runs out of storage.
    _run_isolated_retention(
        session_home,
        paths,
        phase="pre-launch",
        preserve_home=paths.home,
    )

    # Kernel-only inspection happens before provisioning, MCP registration,
    # hooks, AGENTS.md, skills config, or the lifecycle sentinel is written.
    # Linux can attribute native Codex attachments through procfs before the
    # TokenPak lifecycle lease exists.  On other platforms, deterministic
    # workspace homes rely on that exclusive lifecycle lease; shared mode
    # still fails closed through the diagnostic surface when databases exist.
    needs_kernel_preflight = paths.mode == session_home.MODE_SHARED or sys.platform.startswith(
        "linux"
    )
    lock_exit = _preflight_state_lock(home=paths.home) if needs_kernel_preflight else None
    if lock_exit is not None:
        if receipt_out and run_id:
            setup = (
                _receipt_only_setup_metadata()
                if receipt_only
                else {
                    "setup_completed": False,
                    "session_mode": paths.mode,
                    "codex_home": str(paths.home),
                }
            )
            try:
                _write_accounting_receipt(
                    receipt_out=receipt_out,
                    run_id=run_id,
                    codex_args=args,
                    setup=setup,
                    started_at=utc_now(),
                    start_monotonic=time.monotonic(),
                    exit_code=lock_exit,
                    status="blocked",
                    missing_evidence=["codex_process_not_launched_preflight_block"],
                )
            except (OSError, RuntimeError) as exc:
                print(
                    f"tokenpak: failed to write accounting receipt: {exc}",
                    file=sys.stderr,
                )
                return 1
        return lock_exit

    try:
        try:
            lease = session_home.SessionLease.acquire(paths)
        except (OSError, RuntimeError) as exc:
            if not _is_storage_pressure(exc):
                raise
            _run_isolated_retention(
                session_home,
                paths,
                phase="storage-pressure",
                preserve_home=paths.home,
                remove_all_orphans=True,
            )
            lease = session_home.SessionLease.acquire(paths)
    except (OSError, RuntimeError) as exc:
        print(f"tokenpak: selected-home setup refused: {exc}", file=sys.stderr)
        return 1

    with _lease_with_post_retention(lease, session_home, paths):

        def reusable_home_is_clear() -> bool:
            if paths.mode == session_home.MODE_ISOLATED:
                return True
            if paths.mode == session_home.MODE_WORKSPACE and not sys.platform.startswith("linux"):
                return True
            lease.assert_home_binding()
            return _preflight_state_lock(home=paths.home) is None

        # Close the preflight-to-lease race for reusable homes.  A native
        # Codex process does not participate in our sentinel guard, so sample
        # kernel attachment state once more while this launcher owns the
        # TokenPak lease and before companion setup starts subprocesses.
        if not reusable_home_is_clear():
            return 1

        try:
            try:
                lease.assert_home_binding()
                provisioned = session_home.provision(paths, home_fd=lease.home_fd)
                lease.assert_home_binding()
            except (OSError, RuntimeError) as exc:
                if not _is_storage_pressure(exc):
                    raise
                _run_isolated_retention(
                    session_home,
                    paths,
                    phase="storage-pressure",
                    preserve_home=paths.home,
                    remove_all_orphans=True,
                )
                lease.assert_home_binding()
                provisioned = session_home.provision(paths, home_fd=lease.home_fd)
                lease.assert_home_binding()
        except (OSError, RuntimeError) as exc:
            print(f"tokenpak: selected-home provisioning refused: {exc}", file=sys.stderr)
            return 1

        if provisioned.seeded:
            print(
                f"tokenpak: safe config seeded ({', '.join(provisioned.seeded)})",
                file=sys.stderr,
            )
        if provisioned.linked_credentials:
            print(
                "tokenpak: credential link installed "
                f"({', '.join(provisioned.linked_credentials)})",
                file=sys.stderr,
            )

        if paths.mode == session_home.MODE_ISOLATED:
            _run_isolated_retention(
                session_home,
                paths,
                phase="post-provision",
                preserve_home=paths.home,
            )

        if receipt_only:
            assert receipt_out is not None and run_id is not None
            setup = _receipt_only_setup_metadata()
            setup.update({"session_mode": paths.mode, "codex_home": str(paths.home)})
            env = paths.environment(_vanilla_receipt_env())
            env["TOKENPAK_CODEX_RECEIPT_OUT"] = receipt_out
            env["TOKENPAK_CODEX_RUN_ID"] = run_id
            codex_args = ["codex", *args]
            started_at = utc_now()
            start_monotonic = time.monotonic()
            try:
                lease.assert_home_binding()
                if not reusable_home_is_clear():
                    return 1
                lease.begin_transfer()
                exit_code, usage = _run_codex_process(codex_args, env, on_start=lease.transfer_to)
                status = (
                    "completed"
                    if exit_code == 0
                    else "interrupted"
                    if exit_code == 130
                    else "failed"
                )
            except (OSError, RuntimeError) as exc:
                exit_code = 1
                usage = empty_usage()
                status = "launch_failed"
                print(f"tokenpak: failed to launch codex: {exc}", file=sys.stderr)
            try:
                _write_accounting_receipt(
                    receipt_out=receipt_out,
                    run_id=run_id,
                    codex_args=args,
                    setup=setup,
                    started_at=started_at,
                    start_monotonic=start_monotonic,
                    exit_code=exit_code,
                    status=status,
                    usage=usage,
                )
            except OSError as exc:
                print(
                    f"tokenpak: failed to write accounting receipt: {exc}",
                    file=sys.stderr,
                )
                return 1
            return exit_code

        config = CompanionConfig.from_env()
        config.profile_overrides()
        config.journal_dir.mkdir(parents=True, exist_ok=True)

        from .rates_snapshot import refresh as refresh_rates

        rates_path = refresh_rates()
        print(f"tokenpak: rates snapshot refreshed ({rates_path})", file=sys.stderr)

        from .mcp_config import _register, get_env_vars

        env_vars = get_env_vars(config)
        lease.assert_home_binding()
        if not reusable_home_is_clear():
            return 1
        mcp_registered = _register(env_vars=env_vars, codex_home=paths.home)
        lease.assert_home_binding()
        print(
            "tokenpak: MCP server registered"
            if mcp_registered
            else "tokenpak: MCP registration failed (continuing)",
            file=sys.stderr,
        )

        hooks_installed = False
        if config.hooks_enabled:
            from .hooks import _ensure_hooks_feature_enabled, _install_hooks

            lease.assert_home_binding()
            if not reusable_home_is_clear():
                return 1
            if _ensure_hooks_feature_enabled(codex_home=paths.home):
                if not reusable_home_is_clear():
                    return 1
                hooks_path = _install_hooks(target="global", codex_home=paths.home)
                lease.assert_home_binding()
                hooks_installed = True
                print(f"tokenpak: hooks installed ({hooks_path})", file=sys.stderr)
            else:
                print(
                    "tokenpak: hooks feature could not be enabled",
                    file=sys.stderr,
                )

        from .agents_md import _install_agents_md

        lease.assert_home_binding()
        if not reusable_home_is_clear():
            return 1
        agents_path = _install_agents_md(target="global", codex_home=paths.home)
        lease.assert_home_binding()
        print(f"tokenpak: AGENTS.md installed ({agents_path})", file=sys.stderr)

        from .skills_installer import _configure_skills, install_skills

        installed = install_skills(target_dir=paths.skills_root)
        configured = []
        if paths.mode != session_home.MODE_SHARED:
            lease.assert_home_binding()
            configured = _configure_skills(paths.config, skills_root=paths.skills_root)
            lease.assert_home_binding()
        if installed:
            print(
                f"tokenpak: {len(installed)} skills installed and "
                f"{len(configured)} configured ({paths.skills_root})",
                file=sys.stderr,
            )

        setup: dict[str, object] = {
            "setup_completed": True,
            "profile": config.profile,
            "budget_daily_usd": config.budget_daily_usd,
            "session_mode": paths.mode,
            "codex_home": str(paths.home),
            "config_path": str(paths.config),
            "mcp_config_path": str(paths.mcp_config),
            "hooks_path": str(paths.hooks),
            "agents_md_path": str(paths.agents),
            "skills_root": str(paths.skills_root),
            "pid_sentinel_path": str(paths.pid_sentinel),
            "rates_snapshot_refreshed": True,
            "mcp_registered": bool(mcp_registered),
            "hooks_enabled": bool(config.hooks_enabled),
            "hooks_installed": hooks_installed,
            "agents_md_installed": True,
            "skills_installed_count": len(installed),
            "skills_configured_count": len(configured),
        }

        budget_phrase = (
            f"budget ${config.budget_daily_usd:.2f}/day"
            if config.budget_daily_usd > 0
            else "no budget cap"
        )
        print(
            f"tokenpak: companion ready for codex ({config.profile}, {budget_phrase})",
            file=sys.stderr,
        )

        if install_only:
            if receipt_out and run_id:
                try:
                    _write_accounting_receipt(
                        receipt_out=receipt_out,
                        run_id=run_id,
                        codex_args=[],
                        setup=setup,
                        started_at=utc_now(),
                        start_monotonic=time.monotonic(),
                        exit_code=0,
                        status="setup_only",
                        missing_evidence=["codex_process_not_launched_install_only"],
                    )
                except OSError as exc:
                    print(
                        f"tokenpak: failed to write accounting receipt: {exc}",
                        file=sys.stderr,
                    )
                    return 1
            print(
                "tokenpak: setup complete — run `tokenpak codex doctor` to verify",
                file=sys.stderr,
            )
            return 0

        env = paths.environment(os.environ.copy())
        env.update(env_vars)
        if receipt_out and run_id:
            env["TOKENPAK_CODEX_RECEIPT_OUT"] = receipt_out
            env["TOKENPAK_CODEX_RUN_ID"] = run_id

        mode, state_warning = _launcher_mode_state()
        if state_warning:
            print(
                "tokenpak WARNING: invalid launcher permission state: "
                f"{state_warning}; using inherit.",
                file=sys.stderr,
            )
        forwarded, mode_flags, skip_reason, effective_mode = _apply_launcher_mode(
            args,
            mode,
            env,
        )
        banner = _launcher_mode_banner(effective_mode, mode_flags, skip_reason)
        if banner:
            print(banner, file=sys.stderr)
        codex_args = ["codex", *forwarded]
        started_at = utc_now()
        start_monotonic = time.monotonic()
        try:
            lease.assert_home_binding()
            if not reusable_home_is_clear():
                return 1
            lease.begin_transfer()
            exit_code, usage = _run_codex_process(codex_args, env, on_start=lease.transfer_to)
            status = (
                "completed" if exit_code == 0 else "interrupted" if exit_code == 130 else "failed"
            )
        except (OSError, RuntimeError) as exc:
            exit_code = 1
            usage = empty_usage()
            status = "launch_failed"
            print(f"tokenpak: failed to launch codex: {exc}", file=sys.stderr)

        if receipt_out and run_id:
            try:
                _write_accounting_receipt(
                    receipt_out=receipt_out,
                    run_id=run_id,
                    codex_args=forwarded,
                    setup=setup,
                    started_at=started_at,
                    start_monotonic=start_monotonic,
                    exit_code=exit_code,
                    status=status,
                    usage=usage,
                )
            except OSError as exc:
                print(
                    f"tokenpak: failed to write accounting receipt: {exc}",
                    file=sys.stderr,
                )
                return 1
        return exit_code
