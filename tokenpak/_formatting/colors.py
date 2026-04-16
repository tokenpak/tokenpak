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
    WHITE = "\033[38;2;248;250;252m"        # #F8FAFC primary text
    # Brand palette (24-bit truecolor)
    TEAL = "\033[38;2;20;184;166m"          # #14B8A6 primary accent
    PASTEL_YELLOW = "\033[38;2;244;231;161m"  # #F4E7A1 secondary accent (sparse)
    LIGHT_GRAY = "\033[38;2;148;163;184m"   # #94A3B8 muted text
    # State colors
    SUCCESS = "\033[38;2;45;212;191m"       # #2DD4BF
    WARNING = "\033[38;2;250;204;21m"       # #FACC15
    ERROR = "\033[38;2;248;113;113m"        # #F87171


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
