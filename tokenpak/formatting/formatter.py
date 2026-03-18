"""Core formatting engine for TokenPak CLI output."""

from __future__ import annotations

import json
from typing import Iterable

from .colors import Color, paint, supports_color
from .modes import OutputMode


class OutputFormatter:
    def __init__(self, section: str, mode: OutputMode = OutputMode.NORMAL, minimal: bool = False):
        self.section = section
        self.mode = mode
        self.minimal = minimal
        self.color = supports_color()

    def header(self) -> str:
        line1 = f"TOKENPAK v0.3.1  |  {self.section}"
        line2 = "─" * 40
        return "\n".join([line1, line2])

    def kv(self, rows: Iterable[tuple[str, str]]) -> str:
        rows = list(rows)
        if not rows:
            return ""
        width = max(len(k) for k, _ in rows)
        return "\n".join(f"{k:<{width}} : {v}" for k, v in rows)

    def signal(self, symbol: str, text: str, tone: str = "info") -> str:
        color = {
            "success": Color.GREEN,
            "warn": Color.YELLOW,
            "error": Color.RED,
            "muted": Color.DIM,
            "info": Color.CYAN,
        }.get(tone, Color.CYAN)
        return paint(f"{symbol} {text}", color, self.color)

    def error_block(self, title: str, reason: str, action: str) -> str:
        return "\n".join(
            [
                self.header(),
                "",
                self.signal("✖", title, tone="error"),
                f"Reason: {reason}",
                f"Action: {action}",
            ]
        )

    def minimal_line(self, cells: Iterable[str]) -> str:
        return " | ".join(str(c) for c in cells)

    def raw(self, payload: dict) -> str:
        return json.dumps(payload, indent=2, sort_keys=True)
