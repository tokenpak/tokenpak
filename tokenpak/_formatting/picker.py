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

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PickerUnavailable(Exception):
    """Raised when interactive picker cannot operate (non-TTY, Windows, etc.)."""


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

_BACK_SENTINEL = "__back__"


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

    # Build the full option list (with optional back entry)
    all_options = list(options)
    back_offset = 0
    if back_label:
        all_options.insert(0, (_BACK_SENTINEL, f"< {back_label}"))
        back_offset = 1

    # Hide cursor
    sys.stdout.write("\033[?25l")

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

            if not visible:
                # No matches — show "no results" message instead of all options
                pass  # visible stays empty, handled in render

            # Clamp index
            if len(visible) == 0:
                idx = 0
            elif idx >= len(visible):
                idx = len(visible) - 1
            if idx < 0:
                idx = 0

            # Viewport scrolling
            term_h, term_w = shutil.get_terminal_size((80, 24))
            # Reserve lines for: header, title, subtitle, filter, footer, padding
            reserved = 6
            if header:
                reserved += header.count("\n") + 1
            if subtitle:
                reserved += 1
            if filterable and filter_text:
                reserved += 1
            max_visible = max(term_h - reserved, 5)

            # Compute scroll window
            if len(visible) <= max_visible:
                scroll_top = 0
                scroll_bot = len(visible)
            else:
                # Keep idx centered in viewport
                half = max_visible // 2
                scroll_top = max(0, idx - half)
                scroll_bot = scroll_top + max_visible
                if scroll_bot > len(visible):
                    scroll_bot = len(visible)
                    scroll_top = scroll_bot - max_visible

            # Render
            buf = []
            buf.append("\033[2J\033[H")  # clear screen + cursor home

            if header:
                buf.append(header)
                buf.append("\n")

            if title:
                buf.append(f"  {paint(title, Color.BOLD, color)}\n")
            if subtitle:
                buf.append(f"  {paint(subtitle, _MUTED, color)}\n")
            if filterable and filter_text:
                buf.append(f"  {paint('Filter:', _MUTED, color)} {filter_text}_\n")
            buf.append("\n")

            # No results message
            if not visible:
                buf.append(f"  {paint('No matching commands found', _MUTED, color)}\n\n")
                buf.append(f"  {paint('[esc] clear search', _MUTED, color)}\n")
                sys.stdout.write("".join(buf))
                sys.stdout.flush()
                key = getch()
                if key in ("escape", "backspace"):
                    filter_text = ""
                elif key == "quit":
                    return None
                elif key == "backspace":
                    filter_text = filter_text[:-1]
                continue

            # Scroll indicator (top)
            if scroll_top > 0:
                buf.append(f"  {paint(f'  ... {scroll_top} more above', _MUTED, color)}\n")

            # Option rows
            window = visible[scroll_top:scroll_bot]
            for vi, (_, val, label) in enumerate(window, start=scroll_top):
                display = label

                if vi == idx:
                    prefix = paint("> ", _ACCENT, color)
                    line = paint(display, _ACCENT, color)
                    buf.append(f"  {prefix}{line}\n")
                else:
                    buf.append(f"    {display}\n")

            # Scroll indicator (bottom)
            if scroll_bot < len(visible):
                remaining = len(visible) - scroll_bot
                buf.append(f"  {paint(f'  ... {remaining} more below', _MUTED, color)}\n")

            buf.append("\n")

            # Footer / key hints
            if footer:
                buf.append(f"  {paint(footer, _MUTED, color)}\n")
            else:
                hints = ["[arrows] navigate", "[enter] select"]
                if back_label:
                    hints.append("[esc] back")
                if filterable:
                    hints.append("[type] filter")
                hints.append("[q] quit")
                buf.append(f"  {paint('  '.join(hints), _MUTED, color)}\n")

            sys.stdout.write("".join(buf))
            sys.stdout.flush()

            # Input
            key = getch()

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
                # '/' can start filter mode or be treated as filter char
                if filterable:
                    filter_text += "/"
                    idx = 0
            elif filterable and len(key) == 1 and key.isprintable():
                filter_text += key
                idx = 0

    except (KeyboardInterrupt, EOFError):
        return None
    finally:
        # Show cursor
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

    try:
        while True:
            buf = ["\033[2J\033[H"]  # clear + home
            if header:
                buf.append(header)
                buf.append("\n")
            buf.append(f"  {paint(label, Color.BOLD, color)}\n")
            if placeholder and not text:
                buf.append(f"  {paint(placeholder, _MUTED, color)}\n\n")
            buf.append(f"\n  > {text}_\n")
            buf.append(f"\n  {paint('[enter] confirm  [esc] cancel', _MUTED, color)}\n")
            sys.stdout.write("".join(buf))
            sys.stdout.flush()

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
