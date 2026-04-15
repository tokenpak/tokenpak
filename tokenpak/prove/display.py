# SPDX-License-Identifier: Apache-2.0
"""Live display management for prove runs.

Strategy:
  - If already inside tmux ($TMUX set): split current window into panes
  - If GUI desktop ($DISPLAY set): try terminal emulator windows
  - Otherwise: skip live display — inline progress is sufficient

Never creates detached sessions or asks the user to type commands.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def _ensure_tmux() -> None:
    """Install tmux if not present."""
    if shutil.which("tmux"):
        return

    print("  Installing tmux...", file=sys.stderr, end=" ", flush=True)
    for cmd in [
        ["sudo", "apt-get", "install", "-y", "-qq", "tmux"],
        ["sudo", "dnf", "install", "-y", "-q", "tmux"],
        ["sudo", "pacman", "-S", "--noconfirm", "--quiet", "tmux"],
        ["brew", "install", "-q", "tmux"],
    ]:
        pkg_mgr = cmd[1] if cmd[0] == "sudo" else cmd[0]
        if not shutil.which(pkg_mgr):
            continue
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and shutil.which("tmux"):
                print("done.", file=sys.stderr)
                return
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    print("failed.", file=sys.stderr)


class LiveDisplay:
    """Manage live display of two arm log files.

    Automatically picks the best available method with zero user interaction.
    """

    def __init__(self, arm_a_log: Path, arm_b_log: Path) -> None:
        self.arm_a_log = arm_a_log
        self.arm_b_log = arm_b_log
        self._method: Optional[str] = None
        self._tmux_pane_id: Optional[str] = None
        self._subprocesses: list[subprocess.Popen] = []

    def start(self) -> Optional[str]:
        """Start the live display. Returns a description, or None if skipped."""
        self.arm_a_log.parent.mkdir(parents=True, exist_ok=True)
        self.arm_b_log.parent.mkdir(parents=True, exist_ok=True)
        self.arm_a_log.touch()
        self.arm_b_log.touch()

        if not shutil.which("tmux"):
            _ensure_tmux()

        # Best: already inside tmux — split the current window
        if os.environ.get("TMUX") and shutil.which("tmux"):
            if self._try_tmux_split():
                return "Split pane opened (right side)"

        # Good: GUI desktop — open terminal windows
        if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            if self._try_terminal_windows():
                return "Terminal window opened"

        # Otherwise: skip live display, inline progress is enough
        self._method = None
        return None

    def stop(self) -> None:
        """Clean up — close panes we opened, terminate subprocesses."""
        if self._method == "tmux-split" and self._tmux_pane_id:
            subprocess.run(
                ["tmux", "kill-pane", "-t", self._tmux_pane_id],
                capture_output=True,
            )
        for p in self._subprocesses:
            try:
                p.terminate()
            except Exception:
                pass

    def _try_tmux_split(self) -> bool:
        """Split the current tmux window — shows logs in a right pane."""
        try:
            # Create a vertical split showing both logs stacked
            result = subprocess.run(
                ["tmux", "split-window", "-h", "-l", "50%",
                 f"echo '  Arm A (baseline)'; echo '─────────────────'; "
                 f"tail -f {self.arm_a_log} & "
                 f"echo ''; echo '  Arm B (w/ TokenPak)'; echo '─────────────────'; "
                 f"tail -f {self.arm_b_log}; wait"],
                capture_output=True, text=True, check=True,
            )
            # Focus back to the original pane (left side)
            subprocess.run(["tmux", "select-pane", "-L"], capture_output=True)

            # Get the pane ID so we can clean it up later
            pane_result = subprocess.run(
                ["tmux", "display-message", "-p", "-t", ":.+", "#{pane_id}"],
                capture_output=True, text=True,
            )
            self._tmux_pane_id = pane_result.stdout.strip() or None

            self._method = "tmux-split"
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _try_terminal_windows(self) -> bool:
        """Launch a terminal emulator window showing both logs."""
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            return False

        for term_cmd in ["gnome-terminal", "xfce4-terminal", "konsole", "xterm"]:
            if not shutil.which(term_cmd):
                continue
            try:
                tail_script = (
                    f"echo '  Arm A (baseline)'; echo '─────────────────'; "
                    f"tail -f {self.arm_a_log} & "
                    f"echo ''; echo '  Arm B (w/ TokenPak)'; echo '─────────────────'; "
                    f"tail -f {self.arm_b_log}; wait"
                )
                if term_cmd == "gnome-terminal":
                    args = [term_cmd, "--title", "TokenPak Test — Live View",
                            "--", "bash", "-c", tail_script]
                else:
                    args = [term_cmd, "-e", f"bash -c '{tail_script}'"]

                p = subprocess.Popen(args, stderr=subprocess.DEVNULL)
                import time as _time
                _time.sleep(0.3)
                if p.poll() is not None:
                    continue

                self._subprocesses.append(p)
                self._method = "terminal"
                return True
            except (FileNotFoundError, OSError):
                continue

        return False
