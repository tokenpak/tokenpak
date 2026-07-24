"""Governed command-action model for TokenPak automation surfaces.

Trigger and macro automation historically executed config/user-provided command
strings through the host shell (``subprocess.run(cmd, shell=True)``). That is
fragile on Windows because cmd/PowerShell quoting rules differ from POSIX, and it
is a quoting/injection hazard for spaces, quotes, ``$``, ``&``, ``;`` and
event-payload substitution.

This module provides a single ``CommandAction`` abstraction that the trigger and
macro execution surfaces share:

* Normal TokenPak actions are represented as an **argv list** and executed with
  ``shell=False`` (the default). A leading TokenPak subcommand (``status``) is
  prefixed to ``["tokenpak", "status"]``; an explicit external command runs as its
  own argv. Shell metacharacters in a default action are passed **literally** and
  never interpreted.
* A legacy/opt-in **shell** mode is available only for actions explicitly marked
  with the ``shell:`` prefix. This is the sole path that reaches ``shell=True`` and
  it is never the default for a normal TokenPak action.
* Execution preserves dry-run, timeout, and exit-code/output capture so existing
  fire logs keep recording ``returncode`` and combined output.

The symbols here are internal execution plumbing, not part of the released public
CLI/SDK surface, so ``__all__`` is empty and the public-API snapshot omits them.
Consumers import them directly (``from tokenpak.orchestration.commands import
parse_trigger_action``).
"""

from __future__ import annotations

import shlex
import subprocess
import warnings
from dataclasses import dataclass
from typing import List, Mapping, Optional, Sequence, Tuple

# Nothing here is released public API — keep the snapshot surface empty.
__all__: List[str] = []

# Explicit opt-in marker: an action prefixed with ``shell:`` is run through the
# host shell (legacy behavior). This is the only path that uses ``shell=True``.
SHELL_PREFIX = "shell:"

# Static fallback for the TokenPak subcommand set, used only when the live CLI
# registry cannot be imported (e.g. a stripped-down embedding). The authoritative
# source is ``known_tokenpak_subcommands()`` below, which reads the argparse
# registry so newly added CLI verbs are prefixed correctly without touching this
# list. Snapshot of the top-level verb set as of this writing.
TOKENPAK_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "activate",
        "agent",
        "aggregate",
        "alerts",
        "attribution",
        "audit",
        "benchmark",
        "budget",
        "calibrate",
        "check-alerts",
        "claude",
        "codex",
        "compare",
        "compliance",
        "compress",
        "config",
        "config-check",
        "cost",
        "creds",
        "dashboard",
        "deactivate",
        "debug",
        "demo",
        "diagnose",
        "diff",
        "dispatch",
        "doctor",
        "explain",
        "features",
        "fingerprint",
        "fleet",
        "forecast",
        "goals",
        "help",
        "home",
        "index",
        "init",
        "integrate",
        "last",
        "leaderboard",
        "learn",
        "license",
        "lock",
        "logs",
        "macro",
        "menu",
        "models",
        "monitor",
        "openclaw",
        "optimize",
        "pak",
        "pakplan",
        "permissions",
        "plan",
        "preview",
        "prove",
        "prune",
        "recipe",
        "recommendations",
        "replay",
        "report",
        "requests",
        "restart",
        "retrieval",
        "route",
        "run",
        "savings",
        "search",
        "serve",
        "setup",
        "start",
        "stats",
        "status",
        "stop",
        "telemetry",
        "template",
        "test",
        "timeline",
        "tip",
        "trigger",
        "uninstall",
        "update",
        "upgrade",
        "usage",
        "validate",
        "validate-config",
        "vault",
        "vault-health",
        "version",
        "watch",
    }
)

# Cached live subcommand set (resolved on first use).
_SUBCOMMANDS_CACHE: Optional[frozenset[str]] = None


def known_tokenpak_subcommands() -> frozenset[str]:
    """Return the current set of top-level TokenPak CLI verbs.

    Derived live from the CLI's argparse registry so the prefixing decision in
    :func:`parse_trigger_action` tracks the real command surface as it grows.
    Falls back to the built-in verb catalog, then to the static
    ``TOKENPAK_SUBCOMMANDS`` snapshot, if the CLI registry is unavailable.
    The result is cached for the lifetime of the process.
    """
    global _SUBCOMMANDS_CACHE
    if _SUBCOMMANDS_CACHE is None:
        subcommands = TOKENPAK_SUBCOMMANDS
        try:
            # Imported lazily to avoid a module-level cycle with the CLI core,
            # which itself imports this module inside command handlers.
            from tokenpak._cli_core import _core_command_names, registered_command_names

            try:
                subcommands = frozenset(registered_command_names())
            except Exception:
                subcommands = frozenset(_core_command_names())
        except Exception:
            pass
        _SUBCOMMANDS_CACHE = subcommands
    return _SUBCOMMANDS_CACHE


# Characters whose presence in a non-``shell:`` action signals the author may have
# intended shell behavior (pipes, redirects, command chaining, substitution). In
# the governed default these are passed literally as argv data, so we warn rather
# than silently change meaning.
_SHELL_METACHARS = frozenset(";&|<>$`(){}*?!\n")


class _CommandActionShellWarning(UserWarning):
    """Emitted when an action uses the legacy shell path or contains shell
    metacharacters that the governed (argv) executor will not interpret."""


def _looks_like_path(action: str) -> bool:
    """True when *action* names a script/binary by path rather than a subcommand."""
    if action.startswith(("/", "./", "../", "~")):
        return True
    # Windows drive-letter path, e.g. ``C:\tools\run.bat``.
    if len(action) >= 3 and action[1] == ":" and action[2] in ("\\", "/"):
        return True
    return False


@dataclass(frozen=True)
class CommandAction:
    """A parsed, governed command action.

    Either an argv vector (``use_shell=False`` — the default, ``shell=False``) or a
    legacy shell command string (``use_shell=True`` — opt-in, ``shell=True``).
    """

    argv: Tuple[str, ...] = ()
    shell_command: str = ""
    use_shell: bool = False
    raw: str = ""

    @property
    def display(self) -> str:
        """Human-readable form for logs and dry-run output."""
        if self.use_shell:
            return self.shell_command
        return " ".join(self.argv)

    @property
    def is_empty(self) -> bool:
        return not self.argv and not self.shell_command


@dataclass
class CommandResult:
    """Result of executing a :class:`CommandAction`.

    ``output`` is the combined, stripped stdout+stderr that existing fire logs
    record via ``store.log_fire(trigger, returncode, output)``.
    """

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    output: str = ""
    timed_out: bool = False
    error: str = ""
    dry_run: bool = False

    @property
    def success(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.error


def parse_trigger_action(
    action: str,
    *,
    subcommands: Optional[frozenset[str]] = None,
    warn: bool = True,
) -> CommandAction:
    """Parse a stored trigger/macro action string into a governed CommandAction.

    Default behavior is ``shell=False``:

    * ``"status"``            -> argv ``["tokenpak", "status"]``
    * ``"/usr/bin/backup.sh"``-> argv ``["/usr/bin/backup.sh"]``
    * ``"git status"``        -> argv ``["git", "status"]`` (unknown first token)

    Explicit legacy shell opt-in (the only ``shell=True`` path):

    * ``"shell:tokenpak status | tee log"`` -> shell command ``tokenpak status | tee log``

    When a non-``shell:`` action contains shell metacharacters they are passed
    **literally** as argv data; a ``_CommandActionShellWarning`` is emitted (when
    *warn*) so meaning is not changed silently and the author can opt into
    ``shell:`` if shell semantics were intended.
    """
    if subcommands is None:
        subcommands = known_tokenpak_subcommands()

    raw = action
    stripped = action.strip()

    if not stripped:
        return CommandAction(raw=raw)

    # Explicit legacy shell opt-in.
    if stripped.startswith(SHELL_PREFIX):
        shell_cmd = stripped[len(SHELL_PREFIX) :].strip()
        if warn:
            warnings.warn(
                "Trigger/macro action uses the legacy shell path "
                f"({SHELL_PREFIX!r} prefix); it runs via the host shell "
                "(subprocess shell=True). Treat it as trusted user code.",
                _CommandActionShellWarning,
                stacklevel=2,
            )
        return CommandAction(shell_command=shell_cmd, use_shell=True, raw=raw)

    # Governed default: tokenize into argv and run shell=False.
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        # Unbalanced quotes etc. — fall back to a whitespace split rather than
        # crash; still shell=False so nothing is shell-interpreted.
        tokens = stripped.split()
        if warn:
            warnings.warn(
                "Trigger/macro action could not be parsed as a quoted command; "
                "falling back to a whitespace split and running without a shell.",
                _CommandActionShellWarning,
                stacklevel=2,
            )

    if not tokens:
        return CommandAction(raw=raw)

    # Prefix the TokenPak CLI for bare subcommands (but not for paths).
    if not _looks_like_path(stripped) and tokens[0] in subcommands:
        tokens = ["tokenpak", *tokens]

    if warn and any(ch in _SHELL_METACHARS for ch in stripped):
        warnings.warn(
            "Trigger/macro action contains shell metacharacters that the governed "
            "executor passes literally (no shell interpretation). Prefix the action "
            f"with {SHELL_PREFIX!r} if you intend shell behavior.",
            _CommandActionShellWarning,
            stacklevel=2,
        )

    return CommandAction(argv=tuple(tokens), use_shell=False, raw=raw)


def argv_action(argv: Sequence[str]) -> CommandAction:
    """Build a shell-free CommandAction directly from an argv sequence."""
    return CommandAction(argv=tuple(str(part) for part in argv), use_shell=False)


def run_command_action(
    action: CommandAction,
    *,
    timeout: int = 30,
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[str] = None,
    dry_run: bool = False,
) -> CommandResult:
    """Execute *action*, capturing exit code and combined output.

    ``dry_run`` returns a successful, non-executing result. The default
    (``use_shell=False``) runs the argv with ``shell=False``; only an action
    parsed from an explicit ``shell:`` prefix runs with ``shell=True``.
    Missing executables (common with ``shell=False``) and timeouts are reported
    as structured results rather than raised, so daemon threads and CLI callers
    do not crash.
    """
    if dry_run or action.is_empty:
        return CommandResult(returncode=0, dry_run=dry_run)

    run_env = None
    if env is not None:
        import os as _os

        run_env = {**_os.environ, **dict(env)}

    try:
        if action.use_shell:
            proc = subprocess.run(
                action.shell_command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=run_env,
                cwd=cwd,
            )
        else:
            proc = subprocess.run(
                list(action.argv),
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=run_env,
                cwd=cwd,
            )
    except subprocess.TimeoutExpired:
        return CommandResult(returncode=-1, output="timeout", timed_out=True, error="timeout")
    except FileNotFoundError as exc:
        # shell=False does not resolve via the shell; surface a 127-style result.
        return CommandResult(returncode=127, output=str(exc), error=str(exc))
    except OSError as exc:
        return CommandResult(returncode=-2, output=str(exc), error=str(exc))

    combined = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return CommandResult(
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        output=combined,
    )


def run_trigger_action(
    action: str,
    *,
    timeout: int = 30,
    env: Optional[Mapping[str, str]] = None,
    dry_run: bool = False,
    warn: bool = True,
) -> CommandResult:
    """Convenience: parse a stored action string and execute it under the
    governed model. Used by the trigger daemon and CLI fire/test surfaces."""
    return run_command_action(
        parse_trigger_action(action, warn=warn),
        timeout=timeout,
        env=env,
        dry_run=dry_run,
    )
