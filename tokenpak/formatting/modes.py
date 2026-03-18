"""Output mode helpers."""

from __future__ import annotations

from enum import Enum


class OutputMode(str, Enum):
    NORMAL = "normal"
    VERBOSE = "verbose"
    RAW = "raw"


def resolve_mode(args) -> OutputMode:
    value = getattr(args, "output", "normal") or "normal"
    try:
        return OutputMode(value)
    except ValueError:
        return OutputMode.NORMAL
