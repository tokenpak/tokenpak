"""ANSI color helpers with graceful fallback."""

from __future__ import annotations

import os
import sys


class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    DIM = "\033[2m"
    WHITE = "\033[37m"
    # Brand palette (24-bit truecolor)
    TEAL = "\033[38;2;0;181;173m"           # primary accent
    PASTEL_YELLOW = "\033[38;2;255;236;153m"  # secondary accent
    LIGHT_GRAY = "\033[38;2;180;180;180m"   # tertiary / muted text


def supports_color(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.getenv("NO_COLOR"):
        return False
    if os.getenv("TOKENPAK_NO_COLOR") == "1":
        return False
    if os.getenv("FORCE_COLOR"):
        return True
    return bool(getattr(stream, "isatty", lambda: False)())


def paint(text: str, ansi: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{ansi}{text}{Color.RESET}"
