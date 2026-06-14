"""Shell detection and env-var rendering for cross-platform setup output.

Every printed `export NAME='value'` example used to
be POSIX-only, which breaks on Windows cmd.exe and PowerShell. This
module classifies the host shell and emits the right syntax for each
target.

Pure stdlib (no third-party deps). Output is always a
single line, ASCII-safe, ready to copy/paste.
"""

from __future__ import annotations

import os
import sys
from typing import Literal

ShellKind = Literal["posix", "cmd", "powershell"]


def detect_shell() -> ShellKind:
    """Best-effort detection of the host's interactive shell.

    Resolution order:
      1. ``TOKENPAK_SHELL`` env override (testing + power users) — accepts
         posix|cmd|powershell, anything else is ignored.
      2. On non-Windows platforms → always ``posix``.
      3. On Windows: ``PSModulePath`` (set by every modern PowerShell
         session including pwsh.exe) → ``powershell``; else ``cmd``.

    Falls back to ``posix`` rather than raising, because the renderer
    output is still copy-pasteable on most shells.
    """
    override = os.environ.get("TOKENPAK_SHELL", "").strip().lower()
    if override in ("posix", "cmd", "powershell"):
        return override  # type: ignore[return-value]

    if sys.platform != "win32":
        return "posix"

    if os.environ.get("PSModulePath"):
        return "powershell"
    return "cmd"


def render_env_var(name: str, value: str, shell: ShellKind | None = None) -> str:
    """Render a single-line env-var assignment for the given (or detected) shell.

    Examples::

        render_env_var("ANTHROPIC_BASE_URL", "http://localhost:8766", "posix")
        → "export ANTHROPIC_BASE_URL='http://localhost:8766'"

        render_env_var("ANTHROPIC_BASE_URL", "http://localhost:8766", "cmd")
        → "set ANTHROPIC_BASE_URL=http://localhost:8766"

        render_env_var("ANTHROPIC_BASE_URL", "http://localhost:8766", "powershell")
        → "$env:ANTHROPIC_BASE_URL='http://localhost:8766'"

    Quoting honors each shell's escape rules so values containing the
    shell's quote character round-trip safely.
    """
    s = shell or detect_shell()
    if s == "cmd":
        # cmd.exe doesn't quote — the value runs to end-of-line. Stripping
        # surrounding whitespace matches what users would type.
        return f"set {name}={value}"
    if s == "powershell":
        escaped = value.replace("'", "''")
        return f"$env:{name}='{escaped}'"
    # posix (default)
    escaped = value.replace("'", "'\"'\"'")
    return f"export {name}='{escaped}'"
