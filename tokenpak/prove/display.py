# SPDX-License-Identifier: Apache-2.0
"""Live display management for prove runs.

Launches two terminal panes (via tmux, or new terminal windows, or
background tail processes) so the user can watch both arms stream live.

Falls back gracefully: tmux > new terminal windows > inline progress only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


class LiveDisplay:
    """Manage live display of two arm log files.

    Usage::

        display = LiveDisplay(arm_a_log, arm_b_log)
        display.start()    # open panes/windows
        # ... run arms, they write to log files ...
        display.stop()     # clean up

    The display is best-effort — if no terminal multiplexer is available,
    the user gets instructions to tail the logs manually.
    """

    def __init__(self, arm_a_log: Path, arm_b_log: Path) -> None:
        self.arm_a_log = arm_a_log
        self.arm_b_log = arm_b_log
        self._method: Optional[str] = None
        self._tmux_session: Optional[str] = None
        self._subprocesses: list[subprocess.Popen] = []

    def start(self) -> str:
        """Start the live display. Returns a description of what was launched."""
        # Ensure log files exist (tail -f needs them)
        self.arm_a_log.parent.mkdir(parents=True, exist_ok=True)
        self.arm_b_log.parent.mkdir(parents=True, exist_ok=True)
        self.arm_a_log.touch()
        self.arm_b_log.touch()

        # Try tmux first
        if self._try_tmux():
            return f"tmux attach -t {self._tmux_session}"

        # Try launching terminal windows (Linux desktop)
        if self._try_terminal_windows():
            return "Two terminal windows opened"

        # Fallback: background tail processes (write to separate files)
        self._method = "logs"
        return (
            f"Watch live:\n"
            f"    tail -f {self.arm_a_log}   # Arm A (Direct)\n"
            f"    tail -f {self.arm_b_log}   # Arm B (TokenPak)"
        )

    def stop(self) -> None:
        """Clean up the live display."""
        if self._method == "tmux" and self._tmux_session:
            subprocess.run(
                ["tmux", "kill-session", "-t", self._tmux_session],
                capture_output=True,
            )
        for p in self._subprocesses:
            try:
                p.terminate()
            except Exception:
                pass

    def _try_tmux(self) -> bool:
        """Launch a detached tmux session with split panes."""
        if not shutil.which("tmux"):
            return False

        session = "tokenpak-prove"
        self._tmux_session = session

        # Kill any existing session with this name
        subprocess.run(
            ["tmux", "kill-session", "-t", session],
            capture_output=True,
        )

        try:
            # Create session with Arm A log in first pane
            subprocess.run(
                ["tmux", "new-session", "-d", "-s", session,
                 "-x", "200", "-y", "50",
                 f"echo '  ARM A: Direct API'; echo ''; tail -f {self.arm_a_log}"],
                check=True,
                capture_output=True,
            )
            # Split horizontally and add Arm B log
            subprocess.run(
                ["tmux", "split-window", "-h", "-t", session,
                 f"echo '  ARM B: With TokenPak'; echo ''; tail -f {self.arm_b_log}"],
                check=True,
                capture_output=True,
            )
            self._method = "tmux"
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _try_terminal_windows(self) -> bool:
        """Launch two terminal emulator windows."""
        # Try common Linux terminal emulators
        for term_cmd in ["gnome-terminal", "xfce4-terminal", "konsole", "xterm"]:
            if not shutil.which(term_cmd):
                continue
            try:
                if term_cmd == "gnome-terminal":
                    self._subprocesses.append(subprocess.Popen([
                        term_cmd, "--title", "ARM A: Direct API", "--",
                        "bash", "-c", f"echo 'ARM A: Direct API'; tail -f {self.arm_a_log}; read",
                    ]))
                    self._subprocesses.append(subprocess.Popen([
                        term_cmd, "--title", "ARM B: With TokenPak", "--",
                        "bash", "-c", f"echo 'ARM B: TokenPak'; tail -f {self.arm_b_log}; read",
                    ]))
                elif term_cmd == "xterm":
                    self._subprocesses.append(subprocess.Popen([
                        term_cmd, "-T", "ARM A: Direct API", "-e",
                        f"bash -c 'echo ARM A: Direct API; tail -f {self.arm_a_log}; read'",
                    ]))
                    self._subprocesses.append(subprocess.Popen([
                        term_cmd, "-T", "ARM B: With TokenPak", "-e",
                        f"bash -c 'echo ARM B: TokenPak; tail -f {self.arm_b_log}; read'",
                    ]))
                else:
                    # Generic: most terminals support -e or --command
                    self._subprocesses.append(subprocess.Popen([
                        term_cmd, "-e",
                        f"bash -c 'echo ARM A: Direct API; tail -f {self.arm_a_log}; read'",
                    ]))
                    self._subprocesses.append(subprocess.Popen([
                        term_cmd, "-e",
                        f"bash -c 'echo ARM B: TokenPak; tail -f {self.arm_b_log}; read'",
                    ]))

                self._method = "terminal"
                return True
            except (FileNotFoundError, OSError):
                # Clean up any launched processes
                for p in self._subprocesses:
                    try:
                        p.terminate()
                    except Exception:
                        pass
                self._subprocesses.clear()
                continue

        return False
