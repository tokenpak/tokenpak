"""Session-label rendering contract for the Claude companion.

Two things are pinned here.

**The label must fit the terminal.** The host CLI does not truncate an
over-long ``--name``; it wraps it, which makes its header taller than it
accounts for and lays every row below it out against the wrong width. The
visible symptoms are mid-word breaks, repeated regions in scrollback, and
input-box text that stays on screen after being deleted.

**The label must have exactly one definition.** The colors live in
``tokenpak._formatting.colors``; the launcher renders the label from them and
writes the result to the run dir; the ``SessionStart`` hook replays that file.
A hand-written second copy in the shell hook is what previously let the two
surfaces drift apart.

The PTY test at the bottom is the only check that observes what the host CLI
*actually* renders — everything above it tests our own arithmetic, which is
precisely the assumption that needs independent confirmation. It is opt-in so
it never slows the normal suite, but it is kept in-tree deliberately: a
previous investigation built the equivalent probe, left it on an unmerged
branch, and it had to be rebuilt from scratch.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

import pytest

from tokenpak._formatting.colors import Color
from tokenpak.companion import launcher

HOOK = Path(launcher.__file__).parent / "hooks" / "session_start_name.sh"
SGR = re.compile(r"\x1b\[[0-9;]*m")


def _visible_width(text: str) -> int:
    plain = SGR.sub("", text)
    return sum(
        0
        if unicodedata.combining(ch)
        else (2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1)
        for ch in plain
    )


# ---------------------------------------------------------------------------
# Width clamping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("columns", [200, 120, 80, 60, 40, 39, 30, 24, 20, 10, 1])
def test_label_never_exceeds_terminal(columns):
    """Whatever label we pick must leave room for the host's own chrome."""
    label = launcher._session_label_for_width(columns)
    if label is None:
        return
    assert _visible_width(label) + launcher._LABEL_CHROME_COLUMNS <= columns


def test_wide_terminal_keeps_full_branding():
    assert launcher._session_label_for_width(80) == launcher._DEFAULT_SESSION_LABEL


def test_narrow_terminal_degrades_then_drops():
    assert launcher._session_label_for_width(30) == launcher._SHORT_SESSION_LABEL
    assert launcher._session_label_for_width(12) is None


def test_emoji_counted_as_wide():
    """We must assume the pack mark is two columns.

    A terminal that draws it narrower only ever leaves more room than we
    reserved. Assuming one column would under-reserve on terminals that draw
    it wide, which is the direction that overflows.
    """
    assert _visible_width("\U0001f4e6") == 2
    assert _visible_width(launcher._DEFAULT_SESSION_LABEL) == 30


def test_user_supplied_name_is_prefixed_and_returned():
    args, label = launcher._resolve_session_name(["--name", "my-session"])
    assert args[-1] == "\U0001f4e6 my-session"
    assert label == "\U0001f4e6 my-session"


def test_no_name_injected_when_nothing_fits(monkeypatch):
    monkeypatch.setattr(launcher, "_terminal_columns", lambda: 8)
    args, label = launcher._resolve_session_name(["--no-update-notifier"])
    assert "--name" not in args
    assert label is None


# ---------------------------------------------------------------------------
# Single definition
# ---------------------------------------------------------------------------


def test_hook_carries_no_escape_sequences():
    """The shell hook must not hand-copy the label or its colors."""
    body = HOOK.read_text()
    assert "\\u001b" not in body
    assert "\033" not in body
    assert "38;2;" not in body
    assert "48;2;" not in body


def test_label_derives_from_the_palette_module():
    """No brand escape may be written inline in the launcher."""
    assert launcher._LBL_TEAL == Color.TEAL
    assert launcher._LBL_GRAY == Color.LIGHT_GRAY
    assert launcher._LBL_WHITE == Color.PAPER
    assert launcher._LBL_BG_BLACK == Color.CHROME_BG


def test_muted_tone_is_the_palette_tone():
    """Guards the off-palette gray that used to be pasted in by hand."""
    assert Color.LIGHT_GRAY == "\033[38;2;107;114;128m"  # tp-mute #6B7280
    assert Color.TEAL == "\033[38;2;0;180;170m"  # tp-accent #00B4AA


def test_hook_replays_exactly_what_the_launcher_wrote(tmp_path):
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "sessionTitle": launcher._DEFAULT_SESSION_LABEL,
        }
    }
    title_file = tmp_path / "session_title.json"
    title_file.write_text(json.dumps(payload, ensure_ascii=True))

    out = subprocess.run(
        ["bash", str(HOOK), str(title_file)],
        input="{}",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0
    got = json.loads(out.stdout)
    assert got["hookSpecificOutput"]["sessionTitle"] == launcher._DEFAULT_SESSION_LABEL


def test_hook_is_silent_when_no_label_fits(tmp_path):
    out = subprocess.run(
        ["bash", str(HOOK), str(tmp_path / "absent.json")],
        input="{}",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0
    assert out.stdout.strip() == ""


def test_writer_removes_payload_when_nothing_fits(tmp_path):
    class _Cfg:
        run_dir = tmp_path

    path = Path(launcher._write_session_title(_Cfg(), launcher._DEFAULT_SESSION_LABEL))
    assert path.is_file()
    launcher._write_session_title(_Cfg(), None)
    assert not path.exists()


# ---------------------------------------------------------------------------
# What the host CLI actually renders (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("TOKENPAK_RENDER_PROBE") != "1" or shutil.which("claude") is None,
    reason="set TOKENPAK_RENDER_PROBE=1 with the claude CLI installed",
)
@pytest.mark.timeout(120)  # booting a real host CLI exceeds the 30s default
@pytest.mark.parametrize("columns", [80, 40, 30, 24])
def test_host_header_fits_terminal(columns):
    """Boot the host CLI under a fixed-size PTY and measure its header.

    Our own width arithmetic agreeing with itself proves nothing about the
    host's layout. Two controls matter when extending this: a width narrower
    than the chrome, and a same-visible-width label with the styling stripped
    — overflow tracks visible length, not the escape sequences, and a
    single-width idle capture will happily certify a label that overflows
    everywhere else.

    Known coverage gaps, deliberately not yet closed:

    * This measures a freshly booted, idle frame only. It does not exercise an
      input edit/delete or a redraw/scroll transition, which is the state the
      original corruption reports described.
    * It does not exercise a resize (SIGWINCH) after boot.
    * The width oracle here is this module's own wide-character table, which is
      the same assumption the host makes — not the width the user's terminal
      actually draws. A terminal that draws the pack mark narrow would not be
      detected by this test.
    """
    import fcntl
    import pty

    # The suite points HOME at a throwaway directory so tests cannot touch
    # real state. This probe is the exception: it drives the real host CLI,
    # which falls back to its first-run onboarding flow — and never paints the
    # header we are here to measure — unless it can read the real profile.
    # Read the home directory from the passwd entry so the patched HOME does
    # not hide it.
    import pwd
    import select
    import signal
    import struct
    import termios
    import time

    real_home = pwd.getpwuid(os.getuid()).pw_dir

    label = launcher._session_label_for_width(columns)
    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - child process
        try:
            # Run from a directory the host CLI already trusts; an unknown cwd
            # stops it at a trust prompt and it never paints the header.
            os.chdir(real_home)
            os.environ.update(
                HOME=real_home,
                TERM="xterm-256color",
                COLUMNS=str(columns),
                LINES="24",
            )
            os.execvp("claude", ["claude"] + (["--name", label] if label else []))
        except BaseException:
            pass
        os._exit(127)  # never fall back into the test body
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, columns, 0, 0))

    buf, answered, deadline = bytearray(), set(), time.time() + 25
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.3)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        buf.extend(chunk)
        # The host queries terminal capabilities and blocks until answered.
        if b"\x1b[c" in chunk and "da" not in answered:
            os.write(fd, b"\x1b[?62;1;6;9;15;22c")
            answered.add("da")
        if b"\x1b[>0q" in chunk and "xt" not in answered:
            os.write(fd, b"\x1bP>|xterm(370)\x1b\\")
            answered.add("xt")
        if len(buf) > 900 and "─".encode() in bytes(buf):
            # The header is still arriving; give it a beat, then drain once
            # more. Breaking straight out here loses the rows we came for.
            time.sleep(1.5)
            try:
                buf.extend(os.read(fd, 65536))
            except OSError:
                pass
            break
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except Exception:
        pass
    os.close(fd)

    csi = re.compile(rb"\x1b\[[0-9;]*[A-Za-z]")
    osc = re.compile(rb"\x1b\][^\x07]*\x07")
    rows = []
    for line in bytes(buf).split(b"\r\r\n"):
        if b"\x1b[?25" in line or b"\x1b7" in line:
            continue  # terminal handshake, not a rendered row
        if "─".encode() not in line and b"Token" not in line:
            continue
        text = osc.sub(b"", csi.sub(b"", line)).decode("utf8", "replace").rstrip("\r")
        if text.strip():
            rows.append(_visible_width(text))

    assert rows, "host CLI produced no header row to measure"
    assert all(r <= columns for r in rows), f"header overflowed {columns}: {rows}"
