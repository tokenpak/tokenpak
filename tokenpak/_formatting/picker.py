# SPDX-License-Identifier: Apache-2.0
"""Reusable interactive arrow-key picker for TokenPak CLI.

Provides terminal-based single-select navigation with:
- Arrow key up/down movement (wrapping)
- Enter to select, Escape/q to quit/go back
- Optional type-to-filter (narrows visible options as you type)
- Optional "Back" option for nested menu navigation
- Viewport scrolling for long lists
- Branded header support
- Graceful non-TTY and Windows fallback

Rendering substrate (cumulative-spec section B, v1.8.0 foundation pass):
- An interactive *session* is wrapped once in the alternate-screen buffer
  (``\\033[?1049h`` / ``\\033[?1049l``) by :class:`AltScreenSession`, so cleared
  frames never pollute the user's real scrollback. Entered ONCE at the
  ``run_menu`` boundary — not per ``pick()`` call (B1).
- Inside a session, frames redraw smoothly with cursor-home + per-line
  ``\\033[K`` + a trailing ``\\033[0J`` instead of a full ``\\033[2J`` clear, so
  there is no flicker and no ghost rows when a filtered frame shrinks (B2).
- Standalone ``pick()`` callers (not inside a session) keep the legacy
  ``\\033[2J\\033[H`` full-clear path (B3 Tier 2), so their behaviour is
  unchanged.
- Too-small terminals drop to a minimal chrome-free list (B5); non-TTY callers
  get a plain numbered list (B3 Tier 3) via :func:`render_plain_list`.

Zero external dependencies — uses raw termios/tty + ANSI escape codes,
matching the pattern established in cli/commands/test.py.
"""

from __future__ import annotations

import shutil
import sys
from typing import Optional

try:
    import termios
    import tty

    _HAS_TERMIOS = True
except ImportError:
    _HAS_TERMIOS = False

from .colors import Color, paint, supports_color

# Brand accent — all picker highlights use teal
_ACCENT = Color.TEAL
_ACCENT2 = Color.PASTEL_YELLOW
_MUTED = Color.LIGHT_GRAY

# Minimal-mode thresholds (spec B5): below these, strip chrome and show only
# the title + option rows + a one-line footer.
_MIN_ROWS = 10
_MIN_COLS = 40

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PickerUnavailable(Exception):
    """Raised when interactive picker cannot operate (non-TTY, Windows, etc.)."""


# ---------------------------------------------------------------------------
# Alternate-screen session (spec B1)
# ---------------------------------------------------------------------------

_BACK_SENTINEL = "__back__"

# True while the alternate-screen buffer is currently displayed. Drives the
# smooth (home+erase) vs full-clear redraw choice in pick()/prompt_input().
_IN_ALT_SESSION = False


def in_alt_session() -> bool:
    """Return True while an alt-screen session is currently displayed."""
    return _IN_ALT_SESSION


def alt_screen_supported() -> bool:
    """Whether we can drive the alternate-screen buffer (interactive TTY)."""
    return bool(_HAS_TERMIOS and sys.stdin.isatty() and sys.stdout.isatty())


class AltScreenSession:
    """Context manager that owns the alternate-screen buffer for one session.

    Enters the alt-screen + hides the cursor ONCE on ``__enter__`` and restores
    the normal buffer + cursor ONCE on ``__exit__`` — including on ``q``/esc/EOF/
    ``^C``/exception, because ``__exit__`` always runs. ``suspend()`` / ``resume()``
    leave and re-enter the alt-screen for a command that must run on the normal
    buffer (lifecycle ``run_and_exit`` / ``suspend_and_return``).

    Every ``\\033[?1049h`` is balanced by exactly one ``\\033[?1049l`` on every
    path (spec H2): an internal ``_alt_active`` flag ensures the leave sequence
    is emitted once and only once whether the user quits, suspends-then-exits,
    or an exception unwinds the stack.
    """

    def __init__(self, *, enabled: Optional[bool] = None) -> None:
        self.enabled = alt_screen_supported() if enabled is None else enabled
        self._alt_active = False

    def _enter_alt(self) -> None:
        global _IN_ALT_SESSION
        if self.enabled and not self._alt_active:
            try:
                sys.stdout.write("\033[?1049h\033[?25l")  # enter alt buffer + hide cursor
                sys.stdout.flush()
                self._alt_active = True
                _IN_ALT_SESSION = True
            except Exception:  # noqa: BLE001 — fall back to Tier 2 cleanly (B3)
                self.enabled = False
                self._alt_active = False
                _IN_ALT_SESSION = False

    def _leave_alt(self) -> None:
        global _IN_ALT_SESSION
        if self._alt_active:
            try:
                sys.stdout.write("\033[?1049l\033[?25h")  # restore normal buffer + cursor
                sys.stdout.flush()
            except Exception:  # noqa: BLE001
                pass
            self._alt_active = False
        _IN_ALT_SESSION = False

    def __enter__(self) -> "AltScreenSession":
        self._enter_alt()
        return self

    def suspend(self) -> None:
        """Leave the alt-screen to run a command on the normal buffer."""
        self._leave_alt()

    def resume(self) -> None:
        """Re-enter the alt-screen after a suspended command."""
        self._enter_alt()

    def __exit__(self, *exc) -> bool:
        self._leave_alt()
        return False


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


def getch() -> str:
    """Read a single keypress, returning a named action string.

    Returns one of: ``"up"``, ``"down"``, ``"enter"``, ``"quit"``,
    ``"escape"``, ``"backspace"``, ``"slash"``, or a single printable
    character (for type-to-filter).

    Raises :class:`PickerUnavailable` if the terminal is not interactive.
    """
    if not _HAS_TERMIOS:
        raise PickerUnavailable("Interactive input requires a Unix terminal (termios)")
    if not sys.stdin.isatty():
        raise PickerUnavailable("Interactive input requires a TTY")

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)

        # ESC sequence (arrow keys, etc.)
        if ch == "\x1b":
            ch2 = sys.stdin.read(1)
            if ch2 == "[":
                ch3 = sys.stdin.read(1)
                return {"A": "up", "B": "down", "C": "right", "D": "left"}.get(ch3, "")
            # Bare escape (no bracket sequence)
            return "escape"

        if ch in ("\r", "\n"):
            return "enter"
        if ch in ("q", "\x03"):  # q or Ctrl-C
            return "quit"
        if ch == "\x7f" or ch == "\x08":  # Backspace / Delete
            return "backspace"
        if ch == "/":
            return "slash"
        # Printable character
        if ch.isprintable():
            return ch
        return ""
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ---------------------------------------------------------------------------
# Pure frame composition (spec A3 / H1 — no I/O, snapshot-testable)
# ---------------------------------------------------------------------------


def _compose_frame(
    *,
    title: str,
    subtitle: str,
    header: str,
    footer: str,
    filter_text: str,
    filterable: bool,
    rows: list[tuple[str, bool]],
    scroll_above: int,
    scroll_below: int,
    no_results: bool,
    color: bool,
    minimal: bool,
    back_label: Optional[str],
) -> list[str]:
    """Build the body of a picker frame as a list of lines (no trailing ``\\n``).

    ``rows`` is a list of ``(display_label, is_selected)``. Pure function: takes
    fully-resolved state and returns text only — no terminal writes, no probes.
    This is the snapshot-test surface for spec H1.
    """
    lines: list[str] = []

    if header and not minimal:
        # header is a pre-built multi-line block; split so each line gets its
        # own erase when emitted smoothly.
        lines.extend(header.split("\n"))

    if title:
        lines.append(f"  {paint(title, Color.BOLD, color)}")
    if subtitle and not minimal:
        lines.append(f"  {paint(subtitle, _MUTED, color)}")
    if filterable and filter_text:
        lines.append(f"  {paint('Filter:', _MUTED, color)} {filter_text}_")
    lines.append("")

    if no_results:
        lines.append(f"  {paint('No matching commands found', _MUTED, color)}")
        lines.append("")
        lines.append(f"  {paint('[esc] clear search', _MUTED, color)}")
        return lines

    if scroll_above > 0 and not minimal:
        lines.append(f"  {paint(f'  ... {scroll_above} more above', _MUTED, color)}")

    for label, selected in rows:
        if selected:
            prefix = paint("> ", _ACCENT, color)
            line = paint(label, _ACCENT, color)
            lines.append(f"  {prefix}{line}")
        else:
            lines.append(f"    {label}")

    if scroll_below > 0 and not minimal:
        lines.append(f"  {paint(f'  ... {scroll_below} more below', _MUTED, color)}")

    lines.append("")

    if minimal:
        hints = "[up/dn] move  [enter] ok  [q] quit"
        lines.append(f"  {paint(hints, _MUTED, color)}")
    elif footer:
        lines.append(f"  {paint(footer, _MUTED, color)}")
    else:
        hint_parts = ["[arrows] navigate", "[enter] select"]
        if back_label:
            hint_parts.append("[esc] back")
        if filterable:
            hint_parts.append("[type] filter")
        hint_parts.append("[q] quit")
        lines.append(f"  {paint('  '.join(hint_parts), _MUTED, color)}")

    return lines


def _emit_frame(lines: list[str], *, smooth: bool) -> None:
    """Write a composed frame, choosing smooth (in-session) vs full-clear."""
    out: list[str] = []
    if smooth:
        out.append("\033[H")  # cursor home — no full clear (B2)
        for ln in lines:
            out.append(ln)
            out.append("\033[K\n")  # erase stale glyphs to end of each line
        out.append("\033[0J")  # erase to end of screen — kills ghost rows
    else:
        out.append("\033[2J\033[H")  # Tier 2 fallback / standalone callers (B3)
        for ln in lines:
            out.append(ln)
            out.append("\n")
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def _strip_ansi(text: str) -> str:
    import re

    return re.sub(r"\033\[[0-9;?]*[A-Za-z]", "", text)


def render_plain_list(title: str, options: list[tuple[str, str]]) -> str:
    """Tier 3 fallback (spec B3): a plain numbered list, no cursor control.

    Used when the terminal is not interactive so ``tokenpak`` still shows the
    available choices instead of only an error.
    """
    out = [title.strip()] if title.strip() else []
    for i, (_val, label) in enumerate(options, start=1):
        clean = _strip_ansi(label)
        out.append(f"  {i}) {clean}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Picker
# ---------------------------------------------------------------------------


def pick(
    title: str,
    options: list[tuple[str, str]],
    *,
    subtitle: str = "",
    header: str = "",
    footer: str = "",
    filterable: bool = False,
    back_label: Optional[str] = None,
    search_aliases: Optional[dict[str, list[str]]] = None,
) -> Optional[str]:
    """Arrow-key single-select picker.

    Parameters
    ----------
    title:
        Heading text for this screen.
    options:
        List of ``(value, display_label)`` tuples.
    subtitle:
        Optional dim line below title.
    header:
        Optional branded header block rendered above the title.
    footer:
        Custom footer text (overrides default key hints).
    filterable:
        If True, typed characters narrow the visible option list.
    back_label:
        If set, prepends a back option; selecting it returns
        :data:`_BACK_SENTINEL` (``"__back__"``).

    Returns
    -------
    str or None
        The ``value`` of the selected option, :data:`_BACK_SENTINEL` if
        back was chosen, or ``None`` if the user quit.

    Raises
    ------
    PickerUnavailable
        If stdin is not a TTY or termios is unavailable.
    """
    if not _HAS_TERMIOS or not sys.stdin.isatty():
        raise PickerUnavailable("Interactive picker requires a TTY")

    if not options and back_label is None:
        return None

    color = supports_color()
    filter_text = ""
    idx = 0

    # Build the full option list (back at bottom, not top)
    all_options = list(options)
    if back_label:
        all_options.append((_BACK_SENTINEL, back_label))

    # When already inside an alt-screen session, redraw smoothly; otherwise the
    # standalone caller keeps the legacy full-clear behaviour and we manage the
    # cursor ourselves.
    smooth = _IN_ALT_SESSION
    if not smooth:
        sys.stdout.write("\033[?25l")  # hide cursor (standalone path)

    try:
        while True:
            # Apply filter (matches label text + search aliases)
            if filterable and filter_text:
                ft_lower = filter_text.lower()
                _aliases = search_aliases or {}
                visible = []
                for i, (val, label) in enumerate(all_options):
                    if val == _BACK_SENTINEL:
                        visible.append((i, val, label))
                    elif ft_lower in label.lower():
                        visible.append((i, val, label))
                    elif ft_lower in val.lower():
                        visible.append((i, val, label))
                    elif any(ft_lower in a.lower() for a in _aliases.get(val, [])):
                        visible.append((i, val, label))
            else:
                visible = [(i, val, label) for i, (val, label) in enumerate(all_options)]

            # Clamp index
            if len(visible) == 0:
                idx = 0
            elif idx >= len(visible):
                idx = len(visible) - 1
            if idx < 0:
                idx = 0

            # Measure terminal every render (spec B4 — keypress-driven loop).
            term_h, term_w = shutil.get_terminal_size((80, 24))
            minimal = term_h < _MIN_ROWS or term_w < _MIN_COLS

            # Reserve lines for chrome, then size the viewport.
            reserved = 4 if minimal else 6
            if header and not minimal:
                reserved += header.count("\n") + 1
            if subtitle and not minimal:
                reserved += 1
            if filterable and filter_text:
                reserved += 1
            max_visible = max(term_h - reserved, 3 if minimal else 5)

            if len(visible) <= max_visible:
                scroll_top = 0
                scroll_bot = len(visible)
            else:
                half = max_visible // 2
                scroll_top = max(0, idx - half)
                scroll_bot = scroll_top + max_visible
                if scroll_bot > len(visible):
                    scroll_bot = len(visible)
                    scroll_top = scroll_bot - max_visible

            no_results = len(visible) == 0
            window = visible[scroll_top:scroll_bot]
            rows = [(label, gi == idx) for (gi, _val, label) in window]

            frame = _compose_frame(
                title=title,
                subtitle=subtitle,
                header=header,
                footer=footer,
                filter_text=filter_text,
                filterable=filterable,
                rows=rows,
                scroll_above=scroll_top,
                scroll_below=len(visible) - scroll_bot,
                no_results=no_results,
                color=color,
                minimal=minimal,
                back_label=back_label,
            )
            _emit_frame(frame, smooth=smooth)

            # Input
            key = getch()

            if no_results:
                if key in ("escape", "backspace"):
                    filter_text = ""
                elif key == "quit":
                    return None
                continue

            if key == "up":
                idx = (idx - 1) % len(visible)
            elif key == "down":
                idx = (idx + 1) % len(visible)
            elif key == "enter":
                _, selected_val, _ = visible[idx]
                return selected_val
            elif key == "escape":
                if back_label:
                    return _BACK_SENTINEL
                return None
            elif key == "quit":
                return None
            elif key == "backspace":
                if filterable and filter_text:
                    filter_text = filter_text[:-1]
                    idx = 0
                elif back_label:
                    return _BACK_SENTINEL
            elif key == "slash":
                if filterable:
                    filter_text += "/"
                    idx = 0
            elif filterable and len(key) == 1 and key.isprintable():
                filter_text += key
                idx = 0

    except (KeyboardInterrupt, EOFError):
        return None
    finally:
        if not smooth:
            # Standalone path manages its own cursor; in a session the
            # AltScreenSession restores the cursor on exit.
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()


# ---------------------------------------------------------------------------
# Text input prompt
# ---------------------------------------------------------------------------


def prompt_input(
    label: str,
    *,
    header: str = "",
    placeholder: str = "",
) -> Optional[str]:
    """Prompt user to type a text value. Returns string or None on cancel.

    Shows *label* with a blinking cursor.  The user types freely, Enter
    confirms, Escape cancels.
    """
    if not _HAS_TERMIOS or not sys.stdin.isatty():
        raise PickerUnavailable("Text input requires a TTY")

    color = supports_color()
    text = ""
    smooth = _IN_ALT_SESSION

    try:
        while True:
            lines: list[str] = []
            if header:
                lines.extend(header.split("\n"))
            lines.append(f"  {paint(label, Color.BOLD, color)}")
            if placeholder and not text:
                lines.append(f"  {paint(placeholder, _MUTED, color)}")
                lines.append("")
            lines.append("")
            lines.append(f"  > {text}_")
            lines.append("")
            lines.append(f"  {paint('[enter] confirm  [esc] cancel', _MUTED, color)}")
            _emit_frame(lines, smooth=smooth)

            key = getch()
            if key == "enter":
                return text.strip() if text.strip() else None
            elif key in ("escape", "quit"):
                return None
            elif key == "backspace":
                text = text[:-1]
            elif len(key) == 1 and key.isprintable():
                text += key
    except (KeyboardInterrupt, EOFError):
        return None


# ---------------------------------------------------------------------------
# Confirm dialog
# ---------------------------------------------------------------------------


def confirm(
    message: str,
    *,
    header: str = "",
) -> bool:
    """Show a yes/no confirmation. Returns True if confirmed."""
    result = pick(
        message,
        [("yes", "Yes, continue"), ("no", "No, go back")],
        header=header,
    )
    return result == "yes"
