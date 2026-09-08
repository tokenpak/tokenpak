# SPDX-License-Identifier: Apache-2.0
"""Launch-scoped display lifecycle; never writes native user configuration."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from tokenpak.companion._python_spawn import python_spawn_prefix
from tokenpak.status.binding import ENV

SCRIPTS = Path(__file__).parent


def options(args: list[str], client: str) -> tuple[list[str], str]:
    forwarded = []
    surface = os.environ.get("TOKENPAK_STATUS_SURFACE", "auto")
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            forwarded.extend(args[index:])
            break
        if arg.startswith("--status-surface="):
            surface = arg.split("=", 1)[1]
        elif arg == "--status-surface":
            index += 1
            if index == len(args):
                raise ValueError("--status-surface needs auto, native, tmux or off")
            surface = args[index]
        else:
            forwarded.append(arg)
        index += 1
    if surface not in {"auto", "native", "tmux", "off"}:
        raise ValueError("--status-surface supports auto, native, tmux or off")
    if client == "codex" and surface == "native":
        raise ValueError("Codex has no custom native footer; use --status-surface=tmux")
    return forwarded, surface


def interactive(args: list[str]) -> bool:
    # Native noninteractive/administrative commands keep exact stream semantics.
    commands = {
        "exec",
        "e",
        "review",
        "login",
        "logout",
        "mcp",
        "debug",
        "features",
        "completion",
        "resume",
        "fork",
        "app-server",
        "sandbox",
        "apply",
    }
    excluded = commands - {"resume", "fork"}
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return False
    flags = {
        "--print",
        "-p",
        "--json",
        "--help",
        "-h",
        "--version",
        "-v",
        "--install-only",
        "--receipt-only",
    }
    values = {
        "--model",
        "-m",
        "--config",
        "-c",
        "--cd",
        "-C",
        "--profile",
        "--settings",
        "--session-id",
        "--name",
        "-n",
        "--effort",
        "--ask-for-approval",
        "-a",
        "--sandbox",
        "-s",
    }
    skip_value = False
    positional_seen = False
    for arg in args:
        if skip_value:
            skip_value = False
            continue
        if arg == "--":
            break
        if arg in flags:
            return False
        if arg in values:
            skip_value = True
        elif not arg.startswith("-") and not positional_seen:
            positional_seen = True
            if arg in excluded:
                return False
    return True


def user_statusline(args: list[str]) -> bool:
    paths = [Path.home() / ".claude" / "settings.json"]
    for directory in (Path.cwd(), *Path.cwd().parents):
        paths += [
            directory / ".claude" / "settings.json",
            directory / ".claude" / "settings.local.json",
        ]
    for index, arg in enumerate(args):
        if arg.startswith("--settings="):
            value = arg.split("=", 1)[1]
        elif arg == "--settings" and index + 1 < len(args):
            value = args[index + 1]
        else:
            continue
        try:
            if value.lstrip().startswith("{"):
                if "statusLine" in json.loads(value):
                    return True
            else:
                paths.append(Path(value))
        except (ValueError, TypeError):
            return True
    for path in paths:
        try:
            if "statusLine" in json.loads(path.read_text()):
                return True
        except (OSError, ValueError, TypeError):
            continue
    return False


def preflight(surface: str) -> None:
    if surface == "native" and shutil.which("jq") is None:
        print(
            "tokenpak: jq is missing; the status line will show status unavailable", file=sys.stderr
        )


def start_writer(env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [*python_spawn_prefix(), "-m", "tokenpak.status.worker"],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def stop_writer(process: subprocess.Popen | None) -> None:
    if process is not None:
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            process.terminate()
            process.wait(timeout=2)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def start_panel(env: dict[str, str]) -> str | None:
    pane = env.get("TMUX_PANE", "")
    if not env.get("TMUX") or not re.fullmatch(r"%\d+", pane) or not shutil.which("tmux"):
        return None
    command = shlex.join(["bash", str(SCRIPTS / "panel.sh"), env[ENV]])
    try:
        result = subprocess.run(
            [
                "tmux",
                "split-window",
                "-v",
                "-l",
                "2",
                "-d",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                pane,
                command,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=3,
        )
        owned = result.stdout.strip()
        if result.returncode == 0 and re.fullmatch(r"%\d+", owned):
            return owned
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def stop_panel(pane: str | None, env: dict[str, str]) -> None:
    if pane:
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(
                ["tmux", "kill-pane", "-t", pane],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
