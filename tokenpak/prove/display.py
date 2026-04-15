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


def _ensure_tmux() -> None:
    """Install tmux if not present. tmux is a tokenpak dependency for live
    test display (split-pane log tailing)."""
    if shutil.which("tmux"):
        return

    print("  Installing tmux (required for live test display)...", file=sys.stderr)

    # Try package managers in order of likelihood
    for cmd in [
        ["sudo", "apt-get", "install", "-y", "tmux"],
        ["sudo", "dnf", "install", "-y", "tmux"],
        ["sudo", "yum", "install", "-y", "tmux"],
        ["sudo", "pacman", "-S", "--noconfirm", "tmux"],
        ["brew", "install", "tmux"],
    ]:
        if not shutil.which(cmd[0] if cmd[0] != "sudo" else cmd[1]):
            continue
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and shutil.which("tmux"):
                print("  tmux installed.", file=sys.stderr)
                return
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    print("  Could not install tmux automatically.", file=sys.stderr)


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

        # Install tmux if missing (tokenpak dependency)
        if not shutil.which("tmux"):
            _ensure_tmux()

        # Try tmux first
        if self._try_tmux():
            return f"tmux attach -t {self._tmux_session}"

        # Try launching terminal windows (Linux desktop)
        if self._try_terminal_windows():
            return "Two terminal windows opened"

        # Fallback: log files only — user can tail them in another terminal
        self._method = "logs"
        return (
            f"Review logs after test completes (or tail -f in another terminal):\n"
            f"    {self.arm_a_log}\n"
            f"    {self.arm_b_log}"
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
        """Launch two terminal emulator windows.

        Requires a display server ($DISPLAY or $WAYLAND_DISPLAY).
        Skipped entirely in headless/SSH sessions.
        """
        # Guard: no display server → GUI terminals will fail
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            return False

        for term_cmd in ["gnome-terminal", "xfce4-terminal", "konsole", "xterm"]:
            if not shutil.which(term_cmd):
                continue
            try:
                if term_cmd == "gnome-terminal":
                    args_a = [term_cmd, "--title", "ARM A: Direct", "--",
                              "bash", "-c", f"echo 'ARM A: Direct'; tail -f {self.arm_a_log}; read"]
                    args_b = [term_cmd, "--title", "ARM B: TokenPak", "--",
                              "bash", "-c", f"echo 'ARM B: TokenPak'; tail -f {self.arm_b_log}; read"]
                elif term_cmd == "xterm":
                    args_a = [term_cmd, "-T", "ARM A: Direct", "-e",
                              f"bash -c 'echo ARM A; tail -f {self.arm_a_log}; read'"]
                    args_b = [term_cmd, "-T", "ARM B: TokenPak", "-e",
                              f"bash -c 'echo ARM B; tail -f {self.arm_b_log}; read'"]
                else:
                    args_a = [term_cmd, "-e",
                              f"bash -c 'echo ARM A: Direct; tail -f {self.arm_a_log}; read'"]
                    args_b = [term_cmd, "-e",
                              f"bash -c 'echo ARM B: TokenPak; tail -f {self.arm_b_log}; read'"]

                p_a = subprocess.Popen(args_a, stderr=subprocess.DEVNULL)
                p_b = subprocess.Popen(args_b, stderr=subprocess.DEVNULL)

                # Verify the processes didn't immediately exit (display error)
                import time as _time
                _time.sleep(0.3)
                if p_a.poll() is not None or p_b.poll() is not None:
                    # Failed to stay running — display issue
                    for p in (p_a, p_b):
                        try:
                            p.terminate()
                        except Exception:
                            pass
                    continue

                self._subprocesses.extend([p_a, p_b])
                self._method = "terminal"
                return True

            except (FileNotFoundError, OSError):
                for p in self._subprocesses:
                    try:
                        p.terminate()
                    except Exception:
                        pass
                self._subprocesses.clear()
                continue

        return False
