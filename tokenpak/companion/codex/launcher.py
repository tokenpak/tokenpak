# SPDX-License-Identifier: Apache-2.0
"""Launcher for ``tokenpak codex`` — thin bootstrap for Codex with companion.

Does setup (rate snapshot, MCP registration, hooks install, AGENTS.md,
skills) and either exec-replaces into ``codex`` (default) or exits
after install (``--install-only``).

Before exec-ing a real launch it preflights Codex's own local SQLite
databases (``state_5.sqlite`` / ``logs_2.sqlite``) so a shared ``~/.codex``
that is locked by another (or a suspended) Codex process surfaces an
actionable wait/retry instead of Codex dying on a raw "database is
locked" error. ``--install-only`` skips the preflight — it never exec-s.

Companion features work without the launcher if the user manually
configures MCP, hooks, and AGENTS.md — the launcher is convenience.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time

from ..config import CompanionConfig
from .accounting import (
    build_receipt,
    empty_usage,
    merge_usage,
    usage_from_json_line,
    utc_now,
    write_receipt,
)

_TEAL = "\033[38;2;0;180;170m"
_DIM = "\033[2m"
_RESET = "\033[0m"
_CLEAR_LINE = "\033[2K"
_TOKENPAK_CHATGPT_BASE_URL = "http://127.0.0.1:8766/v1"

_BYPASS_FLAG = "--dangerously-bypass-approvals-and-sandbox"
_BYPASS_ENV_VAR = "TOKENPAK_CODEX_BYPASS_APPROVALS_AND_SANDBOX"
_TRUTHY = {"1", "true", "yes"}


def _bypass_env_enabled(env: dict[str, str] | None = None) -> bool:
    """Return True if the bypass env var is set to a truthy value (case-insensitive)."""
    src = env if env is not None else os.environ
    raw = src.get(_BYPASS_ENV_VAR, "")
    return raw.strip().lower() in _TRUTHY


def _fleet_state_enabled() -> bool:
    """True when TokenPak launcher fleet mode is enabled. Never raises.

    Fleet mode is the runtime unattended-bypass knob stored in
    TokenPak-owned state (~/.config/tokenpak/permissions.toml, set via
    `tokenpak permissions set fleet`). It is launcher-scoped only and
    never persists into ~/.codex/config.toml — the persistent trust level
    (tier) lives there and is managed by `tokenpak permissions`.
    """
    try:
        from tokenpak.cli.commands.permissions import fleet_mode_enabled

        return fleet_mode_enabled()
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
    prober=None,
    interactive: bool | None = None,
    timeout_s: float = _LOCK_WAIT_TIMEOUT_S,
    poll_interval_s: float = _LOCK_POLL_INTERVAL_S,
    esc_pressed=None,
    sleep=None,
    monotonic=None,
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

    prober = prober or state_lock.probe
    esc_pressed = esc_pressed or _drain_esc_pressed
    sleep = sleep or time.sleep
    monotonic = monotonic or time.monotonic
    if interactive is None:
        interactive = _stdin_is_tty()

    status = prober()
    if not status.locked:
        return None

    # A stopped/suspended holder never releases the lock — waiting is
    # futile, so surface direct remediation immediately.
    if status.stopped_pids:
        print(state_lock.remediation_hint(status), file=sys.stderr)
        return 1

    if interactive:
        print(
            "tokenpak: SQLite database is busy. Waiting to connect... "
            "Press Esc to cancel.",
            file=sys.stderr,
        )
    else:
        print(
            "tokenpak: Codex local database is busy; waiting up to "
            f"{int(timeout_s)}s for the holder to release "
            "(set a fresh CODEX_HOME to skip)...",
            file=sys.stderr,
        )

    deadline = monotonic() + timeout_s
    while monotonic() < deadline:
        if interactive and esc_pressed():
            print(
                "tokenpak: cancelled while waiting for the Codex database lock.",
                file=sys.stderr,
            )
            print(state_lock.remediation_hint(status), file=sys.stderr)
            return 130
        sleep(poll_interval_s)
        status = prober()
        if not status.locked:
            return None
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
    codex_args: list[str], env: dict[str, str]
) -> tuple[int, dict[str, int | None]]:
    """Run Codex for explicit receipt mode, teeing JSONL while extracting usage."""
    usage = empty_usage()
    if "--json" not in codex_args:
        completed = subprocess.run(codex_args, env=env)
        return completed.returncode, usage

    proc = subprocess.Popen(
        codex_args,
        env=env,
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        usage = merge_usage(usage, usage_from_json_line(line))
    return proc.wait(), usage


def _vanilla_receipt_env() -> dict[str, str]:
    """Return a child environment with TokenPak companion state stripped."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("TOKENPAK_")
    }


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

    install_only = False
    receipt_only = False
    if "--install-only" in args:
        install_only = True
        args = [a for a in args if a != "--install-only"]
    if "--receipt-only" in args:
        receipt_only = True
        args = [a for a in args if a != "--receipt-only"]

    if receipt_only:
        if not (receipt_out and run_id):
            print(
                "tokenpak: --receipt-only requires --receipt-out and --run-id",
                file=sys.stderr,
            )
            return 2
        if install_only:
            print(
                "tokenpak: --receipt-only cannot be combined with --install-only",
                file=sys.stderr,
            )
            return 2
        setup = _receipt_only_setup_metadata()
        lock_exit = _preflight_state_lock()
        if lock_exit is not None:
            started_at = utc_now()
            start_monotonic = time.monotonic()
            try:
                _write_accounting_receipt(
                    receipt_out=receipt_out,
                    run_id=run_id,
                    codex_args=args,
                    setup=setup,
                    started_at=started_at,
                    start_monotonic=start_monotonic,
                    exit_code=lock_exit,
                    status="blocked",
                    missing_evidence=[
                        "codex_process_not_launched_preflight_block"
                    ],
                )
            except OSError as exc:
                print(
                    f"tokenpak: failed to write accounting receipt: {exc}",
                    file=sys.stderr,
                )
                return 1
            return lock_exit

        env = _vanilla_receipt_env()
        env["TOKENPAK_CODEX_RECEIPT_OUT"] = receipt_out
        env["TOKENPAK_CODEX_RUN_ID"] = run_id
        codex_args = ["codex", *args]
        started_at = utc_now()
        start_monotonic = time.monotonic()
        usage = empty_usage()
        status = "failed"
        try:
            exit_code, usage = _run_codex_process(codex_args, env)
            status = "completed" if exit_code == 0 else "failed"
        except KeyboardInterrupt:
            exit_code = 130
            status = "interrupted"
        except OSError as exc:
            exit_code = 1
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

    # ── Step 0: Refresh model-rate snapshot for shell hooks ──
    from .rates_snapshot import refresh as refresh_rates

    rates_path = refresh_rates()
    print(f"tokenpak: rates snapshot refreshed ({rates_path})", file=sys.stderr)

    # ── Step 1: Register MCP server ──────────────────────────
    from .mcp_config import get_env_vars, register

    env_vars = get_env_vars(config)
    mcp_registered = register(env_vars=env_vars)
    if mcp_registered:
        print("tokenpak: MCP server registered", file=sys.stderr)
    else:
        print("tokenpak: MCP registration failed (continuing)", file=sys.stderr)

    # ── Step 2: Install hooks ────────────────────────────────
    hooks_installed = False
    if config.hooks_enabled:
        from .hooks import ensure_hooks_feature_enabled, install_hooks

        if ensure_hooks_feature_enabled():
            hooks_path = install_hooks(target="global")
            hooks_installed = True
            print(f"tokenpak: hooks installed ({hooks_path})", file=sys.stderr)
        else:
            print(
                "tokenpak: hooks feature could not be enabled",
                file=sys.stderr,
            )

    # ── Step 3: Install AGENTS.md ────────────────────────────
    from .agents_md import install_agents_md

    agents_path = install_agents_md(target="global")
    print(f"tokenpak: AGENTS.md installed ({agents_path})", file=sys.stderr)

    # ── Step 4: Install skills ───────────────────────────────
    from .skills_installer import install_skills

    installed = install_skills()
    if installed:
        print(f"tokenpak: {len(installed)} skills installed", file=sys.stderr)

    setup = {
        "setup_completed": True,
        "profile": config.profile,
        "budget_daily_usd": config.budget_daily_usd,
        "rates_snapshot_refreshed": True,
        "mcp_registered": bool(mcp_registered),
        "hooks_enabled": bool(config.hooks_enabled),
        "hooks_installed": hooks_installed,
        "agents_md_installed": True,
        "skills_installed_count": len(installed),
    }

    # ── Step 5: Banner ───────────────────────────────────────
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
            started_at = utc_now()
            start_monotonic = time.monotonic()
            try:
                _write_accounting_receipt(
                    receipt_out=receipt_out,
                    run_id=run_id,
                    codex_args=[],
                    setup=setup,
                    started_at=started_at,
                    start_monotonic=start_monotonic,
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

    # ── Step 5.5: Preflight Codex local-database lock ────────
    # Only for real launches (install-only returned above): a shared
    # ~/.codex whose state/log SQLite is held by another — or a suspended
    # — Codex process would otherwise fail Codex on a raw "database is
    # locked" error at startup.
    lock_exit = _preflight_state_lock()
    if lock_exit is not None:
        if receipt_out and run_id:
            started_at = utc_now()
            start_monotonic = time.monotonic()
            try:
                _write_accounting_receipt(
                    receipt_out=receipt_out,
                    run_id=run_id,
                    codex_args=args,
                    setup=setup,
                    started_at=started_at,
                    start_monotonic=start_monotonic,
                    exit_code=lock_exit,
                    status="blocked",
                    missing_evidence=["codex_process_not_launched_preflight_block"],
                )
            except OSError as exc:
                print(
                    f"tokenpak: failed to write accounting receipt: {exc}",
                    file=sys.stderr,
                )
                return 1
        return lock_exit

    # ── Step 6: Exec into codex ──────────────────────────────
    if config.budget_daily_usd > 0:
        os.environ["TOKENPAK_COMPANION_BUDGET"] = str(config.budget_daily_usd)

    env = os.environ.copy()
    if config.profile != "balanced":
        env["TOKENPAK_COMPANION_PROFILE"] = config.profile
    default_journal_dir = str(config.journal_dir.__class__.home() / ".tokenpak" / "companion")
    if str(config.journal_dir) != default_journal_dir:
        env["TOKENPAK_COMPANION_JOURNAL_DIR"] = str(config.journal_dir)
    if receipt_out and run_id:
        env["TOKENPAK_CODEX_RECEIPT_OUT"] = receipt_out
        env["TOKENPAK_CODEX_RUN_ID"] = run_id

    fleet = _fleet_state_enabled()
    forwarded = _maybe_inject_bypass_flag(args, env, fleet=fleet)
    banner = _fleet_banner(env, fleet=fleet)
    if banner:
        print(banner, file=sys.stderr)
    codex_args = ["codex", *forwarded]
    if receipt_out and run_id:
        started_at = utc_now()
        start_monotonic = time.monotonic()
        usage = empty_usage()
        status = "failed"
        try:
            exit_code, usage = _run_codex_process(codex_args, env)
            status = "completed" if exit_code == 0 else "failed"
        except KeyboardInterrupt:
            exit_code = 130
            status = "interrupted"
        except OSError as exc:
            exit_code = 1
            status = "launch_failed"
            print(f"tokenpak: failed to launch codex: {exc}", file=sys.stderr)
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

    os.execvpe("codex", codex_args, env)

    print("tokenpak: failed to launch codex", file=sys.stderr)
    return 1
