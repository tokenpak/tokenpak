# SPDX-License-Identifier: Apache-2.0
"""Command lifecycle enum + receipt cards for the interactive menu.

Cumulative-spec section C (command lifecycle) and section I (receipts).

The lifecycle enum replaces the old ad-hoc ``_exec(...) + _wait()`` pattern with
a single, explicit per-command mode that drives the alt-screen choreography:

- ``run_and_exit``        (C1) leave alt-screen, run on the normal buffer, exit
                               the menu with the command's exit code.
- ``run_and_return``      (C2) brief action, honest receipt, return to the loop;
                               failures shown in-session, never carried.
- ``suspend_and_return``  (C3) leave alt-screen, run substantial output on the
                               normal buffer, "Press Enter…", re-enter.
- ``takeover``            (C6) hand control to an interactive session
                               (Claude/Codex) — do not try to return.

Default for any unmapped command = ``run_and_exit`` (C5).

The per-command assignment table below is the ratified menu UX. The
``LIFECYCLE`` keys must be a subset of the real CLI commands — guarded by the
overlay-keys-subset CI test.

Zero external dependencies — stdlib only.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable, Optional


class Lifecycle(str, Enum):
    RUN_AND_EXIT = "run_and_exit"
    RUN_AND_RETURN = "run_and_return"
    SUSPEND_AND_RETURN = "suspend_and_return"
    TAKEOVER = "takeover"


DEFAULT_LIFECYCLE = Lifecycle.RUN_AND_EXIT  # C5

# Per-command lifecycle (ratified menu UX). Keys are real top-level CLI
# commands; validated against the argparse registry by the overlay-subset test.
# The long tail inherits DEFAULT_LIFECYCLE.
LIFECYCLE: dict[str, Lifecycle] = {
    # state-changing, brief, non-blocking -> stay in the guided loop (C2)
    "start": Lifecycle.RUN_AND_RETURN,
    "stop": Lifecycle.RUN_AND_RETURN,
    "restart": Lifecycle.RUN_AND_RETURN,
    "setup": Lifecycle.RUN_AND_RETURN,
    "config": Lifecycle.RUN_AND_RETURN,
    # substantial watched output -> suspend, "Press Enter to return" (C3)
    "demo": Lifecycle.SUSPEND_AND_RETURN,
    # terminal "give me the panel / log" intent -> persist in scrollback (C1)
    "status": Lifecycle.RUN_AND_EXIT,
    "cost": Lifecycle.RUN_AND_EXIT,
    "savings": Lifecycle.RUN_AND_EXIT,
    "doctor": Lifecycle.RUN_AND_EXIT,
    "diagnose": Lifecycle.RUN_AND_EXIT,
    "logs": Lifecycle.RUN_AND_EXIT,
    "help": Lifecycle.RUN_AND_EXIT,
    "version": Lifecycle.RUN_AND_EXIT,
    "update": Lifecycle.RUN_AND_EXIT,
    # hand off to an interactive coding session — control transferred (C6)
    "claude": Lifecycle.TAKEOVER,
    "codex": Lifecycle.TAKEOVER,
}


def lifecycle_for(command: str) -> Lifecycle:
    """Return the lifecycle for *command*, defaulting to ``run_and_exit`` (C5)."""
    return LIFECYCLE.get((command or "").strip().split()[0] if command else "", DEFAULT_LIFECYCLE)


# ---------------------------------------------------------------------------
# Receipts (spec I) — pure stdlib box-drawing, no Rich, no I/O.
# ---------------------------------------------------------------------------

# Unicode light box-drawing chars (never ASCII).
_TL, _TR, _BL, _BR = "┌", "┐", "└", "┘"
_H, _V = "─", "│"
_ML, _MR = "├", "┤"


def receipt_card(
    title: str,
    rows: list[tuple[str, str]],
    *,
    width: int = 46,
    paint: Optional[Callable[[str, str, bool], str]] = None,
    accent: str = "",
    indent: str = "  ",
) -> str:
    """Render a compact receipt card (spec I.1).

    Pure string builder: takes ``(label, value)`` rows and returns the card.
    Colour is applied only if a ``paint(text, ansi, enabled=True)``-style
    callable and an ``accent`` ANSI string are provided; otherwise plain text
    (so this is trivially snapshot-testable with colour off).

    The card never fabricates content — callers pass already-honest values
    (``—`` / ``Unknown`` for unknown metrics, never ``$0.00``; spec I.3 / D7).
    """
    inner = width - 2
    lines: list[str] = []

    def _row(text: str) -> str:
        # Pad/clip the visible text to the inner width (plain text only; colour
        # is not measured here because cards render label/value as plain).
        clipped = text[:inner]
        return f"{indent}{_V}{clipped:<{inner}}{_V}"

    top = f"{indent}{_TL}{_H * inner}{_TR}"
    if paint and accent:
        top = f"{indent}{paint(_TL + _H * inner + _TR, accent, True)}"
    lines.append(top)
    lines.append(_row(f"  {title}"))
    lines.append(f"{indent}{_ML}{_H * inner}{_MR}")
    for label, value in rows:
        cell = f"  {label:<14}{value}"
        lines.append(_row(cell))
    lines.append(f"{indent}{_BL}{_H * inner}{_BR}")
    return "\n".join(lines)


def next_chain(actions: list[str], *, indent: str = "  ") -> str:
    """Render the recommended-action ``Next:`` hint line (spec I.1).

    This is the textual hint shown under a receipt; the interactive selector
    itself is rendered separately by the menu via ``pick()``.
    """
    if not actions:
        return ""
    return f"{indent}Next:  " + "    ".join(actions)
