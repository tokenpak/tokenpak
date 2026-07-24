"""ANSI color helpers with graceful fallback."""

from __future__ import annotations

import os
import sys
from typing import Any


class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    # DIM (faint SGR) is a barred text-effect — do NOT use it in any
    # interactive-menu frame; muted text uses the LIGHT_GRAY foreground instead.
    # Retained only for the legacy OutputFormatter "muted" role + its tests.
    DIM = "\033[2m"
    WHITE = "\033[38;2;248;250;252m"  # #F8FAFC primary text
    # Brand palette (24-bit truecolor) — canonical brand tokens.
    TEAL = "\033[38;2;0;195;137m"  # tp-accent  #00C389 — identity / selection
    PASTEL_YELLOW = "\033[38;2;237;224;133m"  # tp-signal-value #EDE085 (provisional) — savings only
    LIGHT_GRAY = "\033[38;2;107;114;128m"  # tp-mute    #6B7280 — secondary / muted text
    # State colors (not brand tokens — left as-is)
    SUCCESS = "\033[38;2;45;212;191m"  # #2DD4BF
    WARNING = "\033[38;2;250;204;21m"  # #FACC15
    ERROR = "\033[38;2;248;113;113m"  # #F87171


def supports_color(stream: Any = None) -> bool:
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
