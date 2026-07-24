# SPDX-License-Identifier: Apache-2.0
"""Interactive branded command menu — ``tokenpak`` / ``tokenpak menu``.

Task-first design: the home screen shows what users want to *do*, not
internal categories.  Simple commands execute immediately; complex ones
open section menus, detail pickers, or input prompts.

Rendering substrate (v1.8.0 foundation pass):
- The whole interactive session is wrapped once in the alternate-screen buffer
  (:class:`AltScreenSession`), so menu frames never pollute real scrollback (B1).
- Each leaf command runs through :func:`_dispatch`, which applies the command's
  lifecycle (``run_and_exit`` / ``run_and_return`` / ``suspend_and_return`` /
  ``takeover``; spec C) — leaving and re-entering the alt-screen as needed.
- The status strip reads a cached, non-blocking, *honest* snapshot
  (:mod:`menu_status`): unknown metrics render as ``—``, never a fabricated
  ``$0.00`` (truth-over-polish).
"""

from __future__ import annotations

import sys
from typing import Optional

from tokenpak._formatting.colors import Color, paint, supports_color
from tokenpak._formatting.picker import (
    _BACK_SENTINEL,
    AltScreenSession,
    PickerUnavailable,
    getch,
    pick,
    prompt_input,
    render_plain_list,
)

from . import menu_status
from .menu_lifecycle import Lifecycle, lifecycle_for, next_chain, receipt_card

# Section titles are BOLD-neutral: the pale-yellow value color (`tp-signal-value`)
# is reserved for the savings/value metric only and must not decorate titles.
_TITLE = Color.BOLD


# ---------------------------------------------------------------------------
# Menu session + exit signalling
# ---------------------------------------------------------------------------

# Set by run_menu() for the lifetime of one interactive session; lets _dispatch
# leave/re-enter the alt-screen around a command.
_SESSION: Optional[AltScreenSession] = None


class _ExitMenu(Exception):
    """Raised to leave the interactive menu, propagating *code* as exit status."""

    def __init__(self, code: int = 0) -> None:
        self.code = code if isinstance(code, int) else (0 if code is None else 1)


# ---------------------------------------------------------------------------
# Brand header
# ---------------------------------------------------------------------------


def _header() -> str:
    c = supports_color()
    try:
        from tokenpak import __version__
    except ImportError:
        __version__ = "?"
    token = paint("token", Color.WHITE + Color.BOLD, c)
    pak = paint("pak", Color.TEAL + Color.BOLD, c)
    ver = paint(f"v{__version__}", Color.LIGHT_GRAY, c)
    tagline = paint("LLM Proxy with Context Compression", Color.LIGHT_GRAY, c)
    return f"\n  \U0001f4e6 {token}{pak}  {ver}\n  {tagline}\n"


# ---------------------------------------------------------------------------
# Live status strip — cached, non-blocking, honest (spec D + flag #3)
# ---------------------------------------------------------------------------


def _status_strip() -> str:
    """Build the status strip from the cached honest snapshot.

    Never blocks the redraw on a probe and never fabricates a value: an unknown
    spend/savings figure renders as ``—`` (not ``$0.00``).
    """
    c = supports_color()
    s = menu_status.snapshot()
    parts = []

    _state_label = {
        "running": ("Running", Color.SUCCESS),
        "starting": ("Starting…", Color.WARNING),
        "stopped": ("Stopped", Color.LIGHT_GRAY),
        "unknown": ("Unknown", Color.LIGHT_GRAY),
    }
    label, color = _state_label.get(s.state, ("Unknown", Color.LIGHT_GRAY))
    parts.append(paint("Proxy:", Color.LIGHT_GRAY, c) + " " + paint(label, color, c))

    cost_str = f"${s.cost:.2f}" if s.cost is not None else "—"
    parts.append(paint("Today:", Color.LIGHT_GRAY, c) + " " + paint(cost_str, Color.WHITE, c))

    if s.saved is not None:
        saved_str = f"${s.saved:.2f}"
        # Pale yellow (tp-signal-value) is reserved for the savings number only.
        parts.append(
            paint("Saved:", Color.LIGHT_GRAY, c) + " " + paint(saved_str, Color.PASTEL_YELLOW, c)
        )
    else:
        parts.append(paint("Saved:", Color.LIGHT_GRAY, c) + " " + paint("—", Color.LIGHT_GRAY, c))

    return "  " + "   ".join(parts)


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------


def _exec(cmd: str, args: str = "", *, clear: bool = True) -> int:
    """Execute a tokenpak command and show output. Returns its exit code.

    With ``clear=False`` (the lifecycle-dispatch path) the command output
    appends to the restored normal buffer, preserving the user's scrollback.
    """
    full = f"tokenpak {cmd}" + (f" {args}" if args else "")
    c = supports_color()
    if clear:
        sys.stdout.write("\033[2J\033[H")
    sys.stdout.write(f"\n  {_header_compact()}\n")
    sys.stdout.write(f"  {paint(full, Color.TEAL, c)}\n")
    sys.stdout.write(f"  {paint(chr(0x2500) * 40, Color.LIGHT_GRAY, c)}\n\n")
    sys.stdout.write("\033[?25h")
    sys.stdout.flush()

    code = 0
    original = sys.argv[:]
    try:
        argv = ["tokenpak", cmd]
        if args:
            argv.extend(args.split())
        sys.argv = argv
        from tokenpak._cli_core import main as cli_main

        cli_main()
    except SystemExit as _se:
        code = _se.code if isinstance(_se.code, int) else (0 if _se.code is None else 1)
    except Exception as exc:
        print(f"\n  {paint('Something went wrong', Color.ERROR, c)}\n")
        print(f"  {exc}\n")
        print(f"  {paint('Try: tokenpak doctor', Color.LIGHT_GRAY, c)}")
        code = 1
    finally:
        sys.argv = original
    return code


# ---------------------------------------------------------------------------
# Lifecycle dispatch (spec C) + receipts (spec I)
# ---------------------------------------------------------------------------


def _return_prompt(*, return_only: bool = False) -> bool:
    """Show the post-command prompt. Returns True to return to the menu.

    ``return_only`` shows only "Press Enter to return" (run_and_return); the
    full prompt also offers ``q`` to exit (suspend_and_return).
    """
    c = supports_color()
    if return_only:
        msg = "Press Enter to return to TokenPak menu"
    else:
        msg = "Press Enter to return to TokenPak menu, or q to exit"
    sys.stdout.write(f"\n  {paint(msg, Color.LIGHT_GRAY, c)} ")
    sys.stdout.flush()
    try:
        key = getch()
    except (PickerUnavailable, KeyboardInterrupt, EOFError):
        return True
    if not return_only and key == "quit":
        return False
    return True


def _render_receipt(cmd: str) -> None:
    """Render an honest receipt card (spec I) from the live cached snapshot.

    Sourced from ``menu_status.snapshot()`` so values are truthful (never a
    fabricated ``$0.00``); the proxy state drives the title + Next chain.
    """
    c = supports_color()
    base = (cmd or "").strip().split()[0] if cmd else ""
    s = menu_status.snapshot()
    port = menu_status._port()

    if base in ("start", "restart"):
        if s.state == "running":
            title, status_val = "Proxy started", paint("Running", Color.SUCCESS, c)
            nxt = ["Launch Companion", "View savings", "Stop proxy"]
        elif s.state == "starting":
            title, status_val = "Proxy starting", paint("Starting…", Color.WARNING, c)
            nxt = ["Proxy status", "Open doctor"]
        else:
            title, status_val = "Proxy not responding", paint("Unknown", Color.LIGHT_GRAY, c)
            nxt = ["Open doctor", "Run setup"]
        rows = [("Status", status_val), ("Endpoint", f"127.0.0.1:{port}")]
    elif base == "stop":
        title = "Proxy stopped"
        rows = [("Status", paint("Stopped", Color.LIGHT_GRAY, c))]
        nxt = ["Start proxy", "Proxy status"]
    else:
        title = f"{base} — done"
        rows = [("Status", paint("Complete", Color.SUCCESS, c))]
        nxt = ["Proxy status", "Back"]

    card = receipt_card(title, rows, paint=paint, accent=Color.TEAL)
    sys.stdout.write("\n" + card + "\n")
    chain = next_chain(nxt)
    if chain:
        sys.stdout.write(paint(chain, Color.LIGHT_GRAY, c) + "\n")
    sys.stdout.flush()


def _dispatch(cmd: str, args: str = "") -> None:
    """Run a leaf command under its lifecycle (spec C). May raise _ExitMenu.

    - run_and_exit / takeover : leave alt-screen, run, EXIT the menu with code.
    - suspend_and_return      : leave, run, "Enter to return / q to exit", re-enter.
    - run_and_return          : leave, run, honest receipt, return to the loop.
    """
    lc = lifecycle_for(cmd)
    sess = _SESSION

    if lc in (Lifecycle.RUN_AND_EXIT, Lifecycle.TAKEOVER):
        if sess:
            sess.suspend()
        code = _exec(cmd, args, clear=False)
        raise _ExitMenu(code)

    if lc == Lifecycle.SUSPEND_AND_RETURN:
        if sess:
            sess.suspend()
        code = _exec(cmd, args, clear=False)
        if _return_prompt(return_only=False):
            if sess:
                sess.resume()
            return
        raise _ExitMenu(code)

    # RUN_AND_RETURN — brief action, honest receipt, return (exit status reset
    # to 0 on return; spec C4 no-haunting).
    if sess:
        sess.suspend()
    _exec(cmd, args, clear=False)
    _render_receipt(cmd)
    _return_prompt(return_only=True)
    if sess:
        sess.resume()
    return


# ---------------------------------------------------------------------------
# Interactive command dispatch — prompts for required args before running
# ---------------------------------------------------------------------------

# Commands that need a text input before executing
_INPUT_COMMANDS: dict[str, dict[str, str]] = {
    "index": {"label": "Directory to index:", "placeholder": "e.g. ~/projects/myapp"},
    "search": {"label": "Search query:", "placeholder": "e.g. authentication middleware"},
    "calibrate": {"label": "Directory to calibrate:", "placeholder": "e.g. ~/projects/myapp"},
    "validate": {"label": "File to validate:", "placeholder": "e.g. ./output.tokpak"},
    "config-check": {
        "label": "Config file to check:",
        "placeholder": "e.g. ~/.tokenpak/config.yaml",
    },
}

# Commands that need a subcommand picked first.
# Each subcommand can optionally have an "input" key for args it needs.
_SUBCOMMAND_COMMANDS: dict[str, list[tuple[str, str, dict[str, str]]]] = {
    # (subcommand_args, display_label, input_config)
    # input_config: {} means no input needed; {"label": ..., "placeholder": ...} means prompt first
    "route": [
        ("list", "View routing rules", {}),
        ("add", "Add a rule", {}),
        ("test", "Test a rule", {"label": "Prompt text:", "placeholder": "e.g. Explain this code"}),
        ("remove", "Remove a rule", {"label": "Rule ID:", "placeholder": ""}),
    ],
    "budget": [
        ("status", "View budget", {}),
        ("set", "Set limits", {}),
        ("history", "Spend history", {}),
    ],
    "config": [
        ("show", "View config", {}),
        ("validate", "Validate", {}),
        ("init", "Create default", {}),
        ("migrate", "Migrate legacy", {}),
        ("path", "Config path", {}),
    ],
    "recipe": [
        (
            "create",
            "Create recipe",
            {"label": "Recipe name:", "placeholder": "e.g. my-legal-cleanup"},
        ),
        (
            "validate",
            "Validate recipe",
            {"label": "Recipe file:", "placeholder": "e.g. ./my-recipe.yaml"},
        ),
        ("test", "Test recipe", {"label": "Recipe file:", "placeholder": "e.g. ./my-recipe.yaml"}),
        (
            "benchmark",
            "Benchmark recipe",
            {"label": "Recipe file:", "placeholder": "e.g. ./my-recipe.yaml"},
        ),
    ],
    "template": [
        ("list", "List templates", {}),
        ("add", "Add template", {"label": "Template name:", "placeholder": "e.g. code-review"}),
        ("show", "Show template", {"label": "Template name:", "placeholder": ""}),
        ("remove", "Remove template", {"label": "Template name:", "placeholder": ""}),
    ],
    "debug": [
        ("on", "Enable debug", {}),
        ("off", "Disable debug", {}),
        ("status", "Debug status", {}),
    ],
    "learn": [
        ("status", "View patterns", {}),
        ("reset", "Reset patterns", {}),
    ],
    "trigger": [
        ("list", "List triggers", {}),
        ("add", "Add trigger", {}),
        ("remove", "Remove trigger", {"label": "Trigger ID:", "placeholder": ""}),
    ],
    "macro": [
        ("list", "List macros", {}),
        ("create", "Create macro", {}),
        ("run", "Run macro", {"label": "Macro name:", "placeholder": ""}),
        ("show", "Show macro", {"label": "Macro name:", "placeholder": ""}),
    ],
    "fingerprint": [
        ("sync", "Sync fingerprints", {}),
        ("cache", "View cache", {}),
        ("clear-cache", "Clear cache", {}),
    ],
    "lock": [
        ("list", "List locks", {}),
        ("claim", "Claim lock", {"label": "File path:", "placeholder": ""}),
        ("release", "Release lock", {"label": "File path:", "placeholder": ""}),
    ],
    "agent": [
        ("list", "List agents", {}),
        ("register", "Register agent", {"label": "Agent name:", "placeholder": ""}),
        ("locks", "View locks", {}),
    ],
    "retrieval": [
        ("status", "Retrieval status", {}),
        (
            "test",
            "Test retrieval",
            {"label": "Search query:", "placeholder": "e.g. authentication"},
        ),
    ],
    "prove": [
        ("run", "Run value proof", {}),
        ("list", "List scenarios", {}),
        ("providers", "Show providers", {}),
    ],
    "alerts": [
        ("test --channel webhook", "Test webhook", {}),
        ("test --channel slack", "Test Slack", {}),
    ],
    "fleet": [
        ("", "Show fleet status", {}),
    ],
    "permissions": [
        ("show", "View current tiers", {}),
        ("set strict", "Set strict tier", {}),
        ("set standard", "Set standard tier (default)", {}),
        ("set auto", "Set auto tier", {}),
        ("reset", "Reset managed keys + fleet off", {}),
    ],
}


def _exec_interactive(cmd: str, hdr: str) -> None:
    """Smart execution — prompts for input/subcommand if needed, then runs."""
    c = supports_color()

    # Check if command needs text input at top level
    if cmd in _INPUT_COMMANDS:
        cfg = _INPUT_COMMANDS[cmd]
        value = prompt_input(cfg["label"], header=hdr, placeholder=cfg.get("placeholder", ""))
        if not value:
            return
        _dispatch(cmd, value)
        return

    # Check if command needs a subcommand picked
    if cmd in _SUBCOMMAND_COMMANDS:
        subs = _SUBCOMMAND_COMMANDS[cmd]
        label = _POLISHED_LABELS.get(cmd, cmd)
        display_options = [(sub_args, display) for sub_args, display, _ in subs]
        choice = pick(
            paint(label, _TITLE, c),
            display_options,
            header=hdr,
            back_label="Back",
        )
        if choice is None or choice == _BACK_SENTINEL:
            return

        # Find the input config for the selected subcommand
        input_cfg = {}
        for sub_args, _, cfg in subs:
            if sub_args == choice:
                input_cfg = cfg
                break

        # If the subcommand itself needs input, prompt for it
        if input_cfg:
            value = prompt_input(
                input_cfg["label"],
                header=hdr,
                placeholder=input_cfg.get("placeholder", ""),
            )
            if not value:
                return
            _dispatch(cmd, f"{choice} {value}")
        else:
            _dispatch(cmd, choice)
        return

    # No special handling needed — run directly
    _dispatch(cmd)


def _header_compact() -> str:
    c = supports_color()
    token = paint("token", Color.WHITE + Color.BOLD, c)
    pak = paint("pak", Color.TEAL + Color.BOLD, c)
    return f"\U0001f4e6 {token}{pak}"


# ---------------------------------------------------------------------------
# Home menu items
# ---------------------------------------------------------------------------

_HOME_ITEMS = [
    ("start_proxy", "Start proxy"),
    ("run_demo", "Run demo"),
    ("check_health", "Proxy status"),
    ("view_spend", "Spend & savings"),
    ("configure", "Configure"),
    ("permissions", "Permission tier"),
    ("companion", "Companion"),
    ("diagnose", "Troubleshoot"),
    ("browse_all", "Browse all commands"),
]

_SEARCH_ALIASES: dict[str, list[str]] = {
    "start_proxy": ["start", "proxy", "run", "launch", "serve"],
    "run_demo": ["demo", "sample", "example", "test compression"],
    "check_health": ["status", "health", "ping", "alive", "proxy status"],
    "view_spend": ["cost", "spend", "savings", "budget", "money", "price", "usage"],
    "configure": ["config", "settings", "setup", "edit", "route", "recipe", "budget"],
    "permissions": [
        "permission",
        "tier",
        "fleet",
        "strict",
        "standard",
        "auto",
        "approval",
        "sandbox",
        "bypass",
    ],
    "companion": ["claude", "codex", "session", "pak", "capsule", "journal", "mcp", "companion"],
    "diagnose": ["doctor", "diag", "fix", "repair", "debug", "troubleshoot", "health check"],
    "browse_all": ["all", "commands", "search", "find", "list"],
}

# Tier-3 fallback dispatch table (spec B3): map each home-menu item to the
# single canonical CLI command it represents, so a terminal without the
# arrow-key picker (Windows, pipe, dumb term) can still select by number or
# name and run the *real* command path. Pure sub-menu items with no single
# command (``companion`` fans out to ``claude`` / ``codex``) are intentionally
# absent — selecting one prints a launcher hint instead of dispatching.
_HOME_FALLBACK_CMDS: dict[str, str] = {
    "start_proxy": "start",
    "run_demo": "demo",
    "check_health": "status",
    "view_spend": "cost",
    "configure": "config",
    "permissions": "permissions show",
    "diagnose": "doctor",
    "browse_all": "help",
}

# Canonical command names a user may type directly at the fallback prompt but
# that are not 1:1 home items (the Companion item launches one of these two).
_FALLBACK_DIRECT_CMDS: frozenset[str] = frozenset({"claude", "codex"})


# ---------------------------------------------------------------------------
# Section menus
# ---------------------------------------------------------------------------


def _section_demo(hdr: str) -> None:
    c = supports_color()
    while True:
        choice = pick(
            paint("Run demo", _TITLE, c),
            [
                ("", "Run default demo"),
                ("--category python", "Python sample"),
                ("--category javascript", "JavaScript sample"),
                ("--category markdown", "Markdown sample"),
                ("--category config", "Config sample"),
                ("--seed", "Seed demo data"),
                ("--clear", "Reset demo data"),
            ],
            header=hdr,
            subtitle="See compression in action. No API key required.",
            back_label="Back",
        )
        if choice is None or choice == _BACK_SENTINEL:
            return
        if choice == "--clear":
            # Confirm destructive action
            confirm_opts = [("yes", "Yes, reset demo data"), ("no", "No, go back")]
            ans = pick(
                "Reset demo data?",
                confirm_opts,
                header=hdr,
                subtitle="This will remove current demo artifacts and recreate defaults.",
            )
            if ans != "yes":
                continue
        _dispatch("demo", choice)


def _section_spend(hdr: str) -> None:
    c = supports_color()
    while True:
        choice = pick(
            paint("Spend & savings", _TITLE, c),
            [
                ("", "Today"),
                ("--week", "This week"),
                ("--month", "This month"),
                ("--by-model", "By model"),
                ("--export-csv", "Export CSV"),
            ],
            header=hdr,
            subtitle="View usage, spend, and estimated savings.",
            back_label="Back",
        )
        if choice is None or choice == _BACK_SENTINEL:
            return
        _dispatch("cost", choice)


def _section_configure(hdr: str) -> None:
    c = supports_color()
    while True:
        choice = pick(
            paint("Configure", _TITLE, c),
            [
                ("show", "View current config"),
                ("validate", "Validate config"),
                ("init", "Create default config"),
                ("migrate", "Migrate legacy config"),
                ("path", "Show config file path"),
            ],
            header=hdr,
            subtitle="View, validate, or change your configuration.",
            back_label="Back",
        )
        if choice is None or choice == _BACK_SENTINEL:
            return
        _dispatch("config", choice)


def _permission_tier_subtitle() -> str:
    """Live one-line summary of persistent tiers and launcher defaults.

    Persistent-tier values are restricted to strict/standard/auto/custom;
    launcher defaults are separate per-client values. Persistent rows never
    read "fleet" or any launcher mode.
    """
    try:
        from tokenpak.cli.commands.permissions import doctor_rows

        rows, _drift = doctor_rows()
        # Compact: collapse the aligned rows into a single subtitle line.
        return "   ".join(" ".join(r.split()) for r in rows)
    except Exception:
        return "Persistent tier + per-client launcher permission defaults."


def _launcher_client_choice(hdr: str, *, codex_only: bool = False) -> Optional[str]:
    """Pick an explicit launcher client scope for a safety-sensitive change."""
    if codex_only:
        return "codex"
    return pick(
        "Launcher client scope",
        [
            ("codex", "Codex only"),
            ("claude-code", "Claude Code only"),
            ("both", "Both launchers"),
        ],
        header=hdr,
        subtitle="Launcher defaults never modify the clients' persistent config.",
        back_label="Back",
    )


def _section_launcher_permissions(hdr: str) -> None:
    """Interactive per-client launcher-default picker."""
    c = supports_color()
    while True:
        choice = pick(
            paint("Launcher permission defaults", _TITLE, c),
            [
                ("show", "View launcher defaults"),
                ("approval-bypass", "Bypass prompts; keep sandbox limits"),
                ("sandbox-bypass", "Disable sandbox; keep approvals (Codex only)"),
                ("full-bypass", "Disable prompts and sandbox (critical risk)"),
                ("inherit", "Reset launcher defaults to inherit"),
            ],
            header=hdr,
            subtitle=(
                "Bypass modes warn on every launch. Managed policy can still "
                "constrain or reject them."
            ),
            back_label="Back",
        )
        if choice is None or choice == _BACK_SENTINEL:
            return
        if choice == "show":
            _exec("permissions", "show", clear=False)
            continue

        client = _launcher_client_choice(
            hdr,
            codex_only=choice in {"approval-bypass", "sandbox-bypass"},
        )
        if client is None or client == _BACK_SENTINEL:
            continue
        if choice == "inherit":
            _exec(
                "permissions",
                f"launcher inherit --client {client}",
                clear=False,
            )
            continue

        risk = {
            "approval-bypass": (
                "Commands can run without asking inside the remaining sandbox limits."
            ),
            "sandbox-bypass": (
                "Approved commands can access host files, credentials, and network."
            ),
            "full-bypass": (
                "No approval prompts or local sandbox. External isolation is required."
            ),
        }[choice]
        ans = pick(
            f"Set {choice} for {client}?",
            [("yes", "Yes, apply this launcher default"), ("no", "No, go back")],
            header=hdr,
            subtitle=risk,
        )
        if ans != "yes":
            continue
        _exec(
            "permissions",
            f"launcher {choice} --client {client} --yes",
            clear=False,
        )


def _section_permissions(hdr: str) -> None:
    """Permission section — persistent tiers plus launcher-only defaults."""
    c = supports_color()
    while True:
        choice = pick(
            paint("Permission tier", Color.PASTEL_YELLOW, c),
            [
                ("show", "View current tiers"),
                ("set strict", "Strict — prompts for everything"),
                ("set standard", "Standard — accept edits (default)"),
                ("set auto", "Auto — client-specific no-prompt mode"),
                ("launcher", "Launcher-only bypass defaults"),
                ("reset", "Reset managed tiers + launcher defaults"),
            ],
            header=hdr,
            subtitle=_permission_tier_subtitle(),
            back_label="Back",
        )
        if choice is None or choice == _BACK_SENTINEL:
            return
        if choice == "launcher":
            _section_launcher_permissions(hdr)
            continue
        _exec("permissions", choice, clear=False)


def _section_companion(hdr: str) -> None:
    c = supports_color()
    while True:
        choice = pick(
            paint("Companion", _TITLE, c),
            [
                ("claude", "Launch Claude companion"),
                ("codex", "Launch Codex companion"),
            ],
            header=hdr,
            subtitle="Launch AI coding tools with tokenpak optimization active.",
            back_label="Back",
        )
        if choice is None or choice == _BACK_SENTINEL:
            return
        _dispatch(choice)


def _section_diagnose(hdr: str) -> None:
    c = supports_color()
    while True:
        choice = pick(
            paint("Troubleshoot", _TITLE, c),
            [
                ("", "Run diagnostics"),
                ("--fix", "Diagnose and auto-fix"),
                ("--verbose", "Verbose diagnostics"),
                ("--claude-code", "Check companion setup"),
            ],
            header=hdr,
            subtitle="Find and fix issues with your tokenpak setup.",
            back_label="Back",
        )
        if choice is None or choice == _BACK_SENTINEL:
            return
        _dispatch("doctor", choice)


_POLISHED_LABELS: dict[str, str] = {
    "start": "Start proxy",
    "stop": "Stop proxy",
    "restart": "Restart proxy",
    "demo": "Run demo",
    "cost": "View spend & savings",
    "status": "Proxy status",
    "logs": "Recent logs",
    "index": "Index directory",
    "search": "Search indexed content",
    "route": "Manage routing rules",
    "recipe": "Manage compression recipes",
    "template": "Manage prompt templates",
    "budget": "Set budget limits",
    "alerts": "Manage alert channels",
    "goals": "Track savings goals",
    "config": "View & edit config",
    "permissions": "Permission tiers",
    "explain": "Explain workflow profiles",
    "version": "Show version",
    "update": "Update tokenpak",
    "benchmark": "Run benchmarks",
    "calibrate": "Calibrate workers",
    "doctor": "Run diagnostics",
    "diagnose": "Full health check",
    "dashboard": "Live dashboard",
    "timeline": "View savings trend",
    "attribution": "Savings by source",
    "models": "Per-model breakdown",
    "forecast": "Cost projections",
    "debug": "Toggle debug logging",
    "claude": "Launch Claude companion",
    "codex": "Launch Codex companion",
    "test": "Interactive A/B test",
    "prove": "A/B value proof",
    "trigger": "Manage event triggers",
    "macro": "Manage macros",
    "config-check": "Validate config file",
    "validate": "Validate JSON file",
    "diff": "Show context changes",
    "serve": "Start proxy server",
    "replay": "Replay captured sessions",
    "audit": "Audit log management",
    "compliance": "Compliance reports",
    # Internal / advanced — kept but separated
    "fingerprint": "Fingerprint management",
    "agent": "Agent coordination",
    "lock": "File lock management",
    "run": "Schedule macro runs",
    "learn": "View learned patterns",
    "vault-health": "Vault index health",
    "fleet": "Fleet status",
    "aggregate": "Aggregate ledger",
    "requests": "Live request explorer",
    "stats": "Registry stats",
    "retrieval": "Test search retrieval",
    "monitor": "Start live monitor",
}

# Commands shown in the default "Common" view
_COMMON_COMMANDS = {
    "start",
    "stop",
    "restart",
    "demo",
    "cost",
    "status",
    "logs",
    "index",
    "search",
    "route",
    "recipe",
    "budget",
    "config",
    "explain",
    "permissions",
    "version",
    "update",
    "doctor",
    "diagnose",
    "dashboard",
    "timeline",
    "models",
    "forecast",
    "claude",
    "codex",
    "test",
    "prove",
    "benchmark",
    "calibrate",
    "alerts",
    "template",
    "goals",
    "attribution",
    "debug",
}


def _section_browse_all(hdr: str) -> None:
    """Browse all commands — common first, toggle to show advanced."""
    from tokenpak._cli_core import _COMMAND_GROUPS

    c = supports_color()
    show_all = False

    while True:
        cmds_list = []
        aliases: dict[str, list[str]] = {}

        for group_name, cmds in _COMMAND_GROUPS.items():
            for cmd, desc in cmds:
                if not show_all and cmd not in _COMMON_COMMANDS:
                    continue
                label_text = _POLISHED_LABELS.get(cmd, desc)
                label = paint(label_text, Color.WHITE, c)
                cmds_list.append((cmd, label))
                aliases[cmd] = [cmd, desc.lower(), group_name.lower()]

        # Add toggle option
        if show_all:
            cmds_list.append(("__toggle__", paint("Show common only", Color.LIGHT_GRAY, c)))
        else:
            cmds_list.append(("__toggle__", paint("Show all commands", Color.LIGHT_GRAY, c)))

        choice = pick(
            "Common commands" if not show_all else "All commands",
            cmds_list,
            header=hdr,
            subtitle="Type to search",
            filterable=True,
            back_label="Back",
            search_aliases=aliases,
            footer="[enter] select   [esc] back   [q] quit",
        )
        if choice is None or choice == _BACK_SENTINEL:
            return
        if choice == "__toggle__":
            show_all = not show_all
            continue
        _exec_interactive(choice, hdr)


# ---------------------------------------------------------------------------
# Home screen — immediate-execute commands
# ---------------------------------------------------------------------------

_IMMEDIATE = {"start_proxy", "check_health"}


def _handle_home_item(item: str, hdr: str) -> None:
    """Dispatch a home menu item."""
    if item == "start_proxy":
        _dispatch("start")
    elif item == "run_demo":
        _section_demo(hdr)
    elif item == "check_health":
        _dispatch("status")
    elif item == "view_spend":
        _section_spend(hdr)
    elif item == "configure":
        _section_configure(hdr)
    elif item == "permissions":
        _section_permissions(hdr)
    elif item == "companion":
        _section_companion(hdr)
    elif item == "diagnose":
        _section_diagnose(hdr)
    elif item == "browse_all":
        _section_browse_all(hdr)


# ---------------------------------------------------------------------------
# Tier-3 interactive fallback (spec B3) — for terminals without the picker
# ---------------------------------------------------------------------------


def _resolve_fallback_command(text: str) -> Optional[str]:
    """Resolve a typed fallback selection to a canonical CLI command.

    Accepts a home-list number (``"1"``..), a home-item value, a canonical
    command name, or a search alias. Returns the command string to run, the
    sentinel ``"companion"`` for the launcher sub-menu (the caller shows a
    hint), or ``None`` when the input matches nothing.
    """
    # A launcher command (claude / codex) typed directly.
    if text in _FALLBACK_DIRECT_CMDS:
        return text

    # List number -> home-item value.
    numbered = {str(i): val for i, (val, _label) in enumerate(_HOME_ITEMS, start=1)}
    val = numbered.get(text)

    if val is None:
        # Home value, canonical command name, or a search alias.
        for v, _label in _HOME_ITEMS:
            if (
                text == v
                or text == _HOME_FALLBACK_CMDS.get(v)
                or text in _SEARCH_ALIASES.get(v, ())
            ):
                val = v
                break

    if val is None:
        return None
    if val == "companion":
        return "companion"  # caller renders the launcher hint
    return _HOME_FALLBACK_CMDS.get(val)


def _run_plain_fallback() -> None:
    """Tier-3 fallback when the arrow-key picker is unavailable.

    Always renders the same plain numbered list. When stdin AND stdout are an
    interactive TTY it then prompts for a selection and dispatches through the
    real command path (``_exec``); on a non-interactive stream (pipe / CI /
    redirect) it prints the list once and returns without blocking on input —
    preserving the historical display-only behaviour.
    """
    listing = render_plain_list("What do you want to do?", _HOME_ITEMS)
    tail = "\nRun `tokenpak <command>` or `tokenpak help` for the full list."

    # Non-interactive stream: display-only, never block waiting for input.
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(listing)
        print(tail)
        return

    print(listing)
    print(tail)
    prompt = "\n  Select an option (number or name, q to quit) > "
    while True:
        try:
            raw = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not raw:
            continue
        low = raw.lower()
        if low in ("q", "quit", "exit"):
            return

        command = _resolve_fallback_command(low)
        if command is None:
            print(
                f"  Unknown option: {raw!r}. "
                f"Enter 1-{len(_HOME_ITEMS)}, a command name, or q to quit."
            )
            continue
        if command == "companion":
            print("  Companion launches an AI coding tool with tokenpak active.")
            print("  Type `claude` or `codex` to launch one.")
            continue

        cmd, _, rest = command.partition(" ")
        try:
            _exec(cmd, rest, clear=False)
        except KeyboardInterrupt:
            # Ctrl-C out of a running command exits the fallback cleanly.
            print()
            return


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------


def run_menu() -> None:
    """Launch the interactive branded menu."""
    global _SESSION
    try:
        with AltScreenSession() as sess:
            _SESSION = sess
            hdr = _header()

            while True:
                # Build home screen with cached, honest status strip
                status = _status_strip()

                c = supports_color()
                home_options = []
                for val, label in _HOME_ITEMS:
                    home_options.append((val, label))

                # Show CLI command hint on the right for each item
                _CMD_HINTS = {
                    "start_proxy": "tokenpak start",
                    "run_demo": "tokenpak demo",
                    "check_health": "tokenpak status",
                    "view_spend": "tokenpak cost",
                    "configure": "tokenpak config",
                    "permissions": "tokenpak permissions",
                    "companion": "",
                    "diagnose": "tokenpak doctor",
                    "browse_all": "",
                }
                styled_options = []
                for val, label in home_options:
                    hint = _CMD_HINTS.get(val, "")
                    if hint:
                        styled = f"{label:<26}" + paint(hint, Color.LIGHT_GRAY, c)
                    else:
                        styled = label
                    styled_options.append((val, styled))

                choice = pick(
                    "What do you want to do?",
                    styled_options,
                    header=hdr + "\n" + status + "\n",
                    subtitle="Type to search all commands",
                    filterable=True,
                    search_aliases=_SEARCH_ALIASES,
                    footer="Type to search   [enter] select   [q] quit",
                )

                if choice is None:
                    break

                _handle_home_item(choice, hdr)

    except _ExitMenu as exit_signal:
        # Alt-screen already restored by suspend()/__exit__. Propagate the
        # command's exit code so `run_and_exit` honours it (spec C1/C4).
        raise SystemExit(exit_signal.code)
    except PickerUnavailable:
        # Tier 3 fallback (spec B3): show the choices and — on an interactive
        # TTY without the picker — let the user select by number or name.
        _run_plain_fallback()
    except KeyboardInterrupt:
        pass
    finally:
        _SESSION = None
