# SPDX-License-Identifier: Apache-2.0
"""Launch-local session identity shared by native hooks, MCP and displays."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

ENV = "TOKENPAK_COMPANION_SESSION_DIR"
_SESSION = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def valid_session(value: object) -> bool:
    return isinstance(value, str) and _SESSION.fullmatch(value) is not None


def run_dir() -> Path:
    from tokenpak import _paths

    scoped = os.environ.get(ENV)
    return Path(scoped) if scoped else _paths.companion_run_dir()


def current_session() -> str:
    try:
        with (run_dir() / "current-session").open(encoding="ascii") as handle:
            value = handle.read(65).strip()
        return value if valid_session(value) else ""
    except (OSError, UnicodeError):
        return ""


def create_launch_dir(parent: Path) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="session-", dir=parent))


def write_session(value: str) -> None:
    """Replace a marker atomically; reject untrusted path-like identities."""
    if not valid_session(value):
        return
    directory = run_dir()
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".session-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(value)
        os.replace(name, directory / "current-session")
    finally:
        if os.path.exists(name):
            os.unlink(name)
