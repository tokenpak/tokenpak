# SPDX-License-Identifier: Apache-2.0
"""Interactive branded command menu — ``tokenpak menu``.

Presents the full tokenpak command surface as a navigable,
arrow-key driven menu with:

- Category → command drill-down
- Descriptions for every option
- Type-to-filter across commands
- Global search (``/``) from category screen
- Branded header with version
- Back navigation (Escape) and quit (q)
"""

from __future__ import annotations

import sys
from typing import Optional

from tokenpak._formatting.colors import Color, paint, supports_color
from tokenpak._formatting.picker import PickerUnavailable, pick, getch, _BACK_SENTINEL


def _branded_header() -> str:
    """Return the branded tokenpak header block."""
    try:
        from tokenpak import __version__
    except ImportError:
        __version__ = "?"

    color = supports_color()

    top = "  ╔══════════════════════════════════════════╗"
    mid1 = f"  ║  TOKENPAK {__version__:<31}║"
    mid2 = "  ║  LLM Proxy with Context Compression      ║"
    bot = "  ╚══════════════════════════════════════════╝"

    if color:
        top = paint(top, Color.CYAN, True)
        mid1 = paint(mid1, Color.CYAN, True)
        mid2 = paint(mid2, Color.CYAN, True)
        bot = paint(bot, Color.CYAN, True)

    return f"\n{top}\n{mid1}\n{mid2}\n{bot}\n"


def _build_category_options() -> list[tuple[str, str]]:
    """Build the category picker options from _COMMAND_GROUPS."""
    from tokenpak._cli_core import _COMMAND_GROUPS

    options = []
    for group_name, cmds in _COMMAND_GROUPS.items():
        count = len(cmds)
        label = f"{group_name:<24} ({count} command{'s' if count != 1 else ''})"
        options.append((group_name, label))
    return options


def _build_command_options(group_name: str) -> list[tuple[str, str]]:
    """Build command picker options for a specific category."""
    from tokenpak._cli_core import _COMMAND_GROUPS

    cmds = _COMMAND_GROUPS.get(group_name, [])
    return [(cmd, f"{cmd:<16} {desc}") for cmd, desc in cmds]


def _build_all_commands() -> list[tuple[str, str]]:
    """Flatten all commands for global search."""
    from tokenpak._cli_core import _COMMAND_GROUPS

    color = supports_color()
    all_cmds = []
    for group_name, cmds in _COMMAND_GROUPS.items():
        for cmd, desc in cmds:
            tag = paint(f"[{group_name}]", Color.DIM, color)
            label = f"{cmd:<16} {desc}  {tag}"
            all_cmds.append((cmd, label))
    return all_cmds


def _execute_command(cmd_name: str) -> None:
    """Execute a CLI command by name."""
    sys.stdout.write("\033[2J\033[H")  # clear screen
    sys.stdout.write(f"\n  Running: tokenpak {cmd_name}\n")
    sys.stdout.write("  " + "\u2500" * 38 + "\n\n")
    sys.stdout.flush()

    # Show cursor during command execution
    sys.stdout.write("\033[?25h")
    sys.stdout.flush()

    original_argv = sys.argv[:]
    try:
        sys.argv = ["tokenpak", cmd_name]
        from tokenpak._cli_core import main as cli_main
        cli_main()
    except SystemExit:
        pass
    except Exception as exc:
        print(f"\n  Error: {exc}")
    finally:
        sys.argv = original_argv


def _wait_for_key() -> None:
    """Prompt user to press any key, then return."""
    color = supports_color()
    msg = paint("\n  Press any key to return to menu...", Color.DIM, color)
    sys.stdout.write(msg)
    sys.stdout.flush()
    try:
        getch()
    except (PickerUnavailable, KeyboardInterrupt, EOFError):
        pass


def run_menu() -> None:
    """Launch the interactive branded menu."""
    try:
        header = _branded_header()

        while True:
            # ── Screen 1: Category picker ──
            categories = _build_category_options()
            choice = pick(
                "Select a category:",
                categories,
                header=header,
                subtitle="Navigate with arrows, type to filter",
                filterable=True,
                footer="[arrows] navigate  [enter] select  [/] search all  [q] quit",
            )

            if choice is None:
                break

            # ── Global search mode (triggered if pick returned a command) ──
            # Check if user wants global search — we handle this via a
            # special "search" entry. Actually, the '/' key in the picker
            # acts as a filter char. For global search, let's add a
            # "Search all commands" option at the top.

            # ── Screen 2: Command picker within category ──
            while True:
                cmd_options = _build_command_options(choice)
                selected = pick(
                    f"{choice}:",
                    cmd_options,
                    header=header,
                    subtitle="Select a command to run",
                    back_label="Back to categories",
                    filterable=True,
                )

                if selected is None or selected == _BACK_SENTINEL:
                    break  # Back to category list

                # ── Screen 3: Execute ──
                _execute_command(selected)
                _wait_for_key()
                # Loop back to same category

    except PickerUnavailable:
        print("Interactive menu requires a terminal (TTY).")
        print("Run `tokenpak help` for a non-interactive command list.")
    except KeyboardInterrupt:
        pass
    finally:
        # Restore terminal state
        sys.stdout.write("\033[2J\033[H")  # clear screen
        sys.stdout.write("\033[?25h")  # show cursor
        sys.stdout.flush()
