# SPDX-License-Identifier: Apache-2.0
"""Interactive branded command menu — ``tokenpak`` / ``tokenpak menu``.

Task-first design: the home screen shows what users want to *do*, not
internal categories.  Simple commands execute immediately; complex ones
open section menus, detail pickers, or input prompts.

Design spec: tokenpak CLI Menu + Branding Spec (v1)
"""

from __future__ import annotations

import sys
from typing import Optional

from tokenpak._formatting.colors import Color, paint, supports_color
from tokenpak._formatting.picker import (
    PickerUnavailable,
    _BACK_SENTINEL,
    getch,
    pick,
    prompt_input,
)


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
    return f"\n  \U0001F4E6 {token}{pak}  {ver}\n  {tagline}\n"


# ---------------------------------------------------------------------------
# Live status strip
# ---------------------------------------------------------------------------

def _status_strip() -> str:
    """Build live status strip from proxy + local state."""
    c = supports_color()
    parts = []

    # Proxy status
    proxy_status = "Stopped"
    proxy_color = Color.LIGHT_GRAY
    try:
        import urllib.request
        resp = urllib.request.urlopen("http://127.0.0.1:8766/health", timeout=1)
        if resp.status == 200:
            proxy_status = "Running"
            proxy_color = Color.SUCCESS
    except Exception:
        pass

    parts.append(paint("Proxy:", Color.LIGHT_GRAY, c) + " " + paint(proxy_status, proxy_color, c))

    # Today's spend
    try:
        import urllib.request, json as _json
        resp = urllib.request.urlopen("http://127.0.0.1:8766/stats", timeout=1)
        data = _json.loads(resp.read())
        cost = data.get("cost", 0)
        saved = data.get("cost_saved", 0)
        parts.append(paint("Today:", Color.LIGHT_GRAY, c) + " " + paint(f"${cost:.2f}", Color.WHITE, c))
        parts.append(paint("Saved:", Color.LIGHT_GRAY, c) + " " + paint(f"${saved:.2f}", Color.PASTEL_YELLOW, c))
    except Exception:
        parts.append(paint("Today:", Color.LIGHT_GRAY, c) + " " + paint("$0.00", Color.WHITE, c))
        parts.append(paint("Saved:", Color.LIGHT_GRAY, c) + " " + paint("$0.00", Color.LIGHT_GRAY, c))

    return "  " + "   ".join(parts)


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------

def _exec(cmd: str, args: str = "") -> None:
    """Execute a tokenpak command and show output."""
    full = f"tokenpak {cmd}" + (f" {args}" if args else "")
    c = supports_color()
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.write(f"\n  {_header_compact()}\n")
    sys.stdout.write(f"  {paint(full, Color.TEAL, c)}\n")
    sys.stdout.write(f"  {paint(chr(0x2500) * 40, Color.LIGHT_GRAY, c)}\n\n")
    sys.stdout.write("\033[?25h")
    sys.stdout.flush()

    original = sys.argv[:]
    try:
        argv = ["tokenpak", cmd]
        if args:
            argv.extend(args.split())
        sys.argv = argv
        from tokenpak._cli_core import main as cli_main
        cli_main()
    except SystemExit:
        pass
    except Exception as exc:
        print(f"\n  {paint('Something went wrong', Color.ERROR, c)}\n")
        print(f"  {exc}\n")
        print(f"  {paint('Try: tokenpak doctor', Color.LIGHT_GRAY, c)}")
    finally:
        sys.argv = original


# ---------------------------------------------------------------------------
# Interactive command dispatch — prompts for required args before running
# ---------------------------------------------------------------------------

# Commands that need a text input before executing
_INPUT_COMMANDS: dict[str, dict] = {
    "index":        {"label": "Directory to index:", "placeholder": "e.g. ~/projects/myapp"},
    "search":       {"label": "Search query:", "placeholder": "e.g. authentication middleware"},
    "calibrate":    {"label": "Directory to calibrate:", "placeholder": "e.g. ~/projects/myapp"},
    "validate":     {"label": "File to validate:", "placeholder": "e.g. ./output.tokpak"},
    "config-check": {"label": "Config file to check:", "placeholder": "e.g. ~/.tokenpak/config.yaml"},
}

# Commands that need a subcommand picked first.
# Each subcommand can optionally have an "input" key for args it needs.
_SUBCOMMAND_COMMANDS: dict[str, list[tuple[str, str, dict]]] = {
    # (subcommand_args, display_label, input_config)
    # input_config: {} means no input needed; {"label": ..., "placeholder": ...} means prompt first
    "route": [
        ("list",    "View routing rules",  {}),
        ("add",     "Add a rule",          {}),
        ("test",    "Test a rule",         {"label": "Prompt text:", "placeholder": "e.g. Explain this code"}),
        ("remove",  "Remove a rule",       {"label": "Rule ID:", "placeholder": ""}),
    ],
    "budget": [
        ("status",  "View budget",    {}),
        ("set",     "Set limits",     {}),
        ("history", "Spend history",  {}),
    ],
    "config": [
        ("show",     "View config",      {}),
        ("validate", "Validate",         {}),
        ("init",     "Create default",   {}),
        ("migrate",  "Migrate legacy",   {}),
        ("path",     "Config path",      {}),
    ],
    "recipe": [
        ("create",    "Create recipe",    {"label": "Recipe name:", "placeholder": "e.g. my-legal-cleanup"}),
        ("validate",  "Validate recipe",  {"label": "Recipe file:", "placeholder": "e.g. ./my-recipe.yaml"}),
        ("test",      "Test recipe",      {"label": "Recipe file:", "placeholder": "e.g. ./my-recipe.yaml"}),
        ("benchmark", "Benchmark recipe", {"label": "Recipe file:", "placeholder": "e.g. ./my-recipe.yaml"}),
    ],
    "template": [
        ("list",   "List templates",   {}),
        ("add",    "Add template",     {"label": "Template name:", "placeholder": "e.g. code-review"}),
        ("show",   "Show template",    {"label": "Template name:", "placeholder": ""}),
        ("remove", "Remove template",  {"label": "Template name:", "placeholder": ""}),
    ],
    "debug": [
        ("on",     "Enable debug",  {}),
        ("off",    "Disable debug", {}),
        ("status", "Debug status",  {}),
    ],
    "learn": [
        ("status", "View patterns", {}),
        ("reset",  "Reset patterns", {}),
    ],
    "trigger": [
        ("list",   "List triggers",  {}),
        ("add",    "Add trigger",    {}),
        ("remove", "Remove trigger", {"label": "Trigger ID:", "placeholder": ""}),
    ],
    "macro": [
        ("list",   "List macros",  {}),
        ("create", "Create macro", {}),
        ("run",    "Run macro",    {"label": "Macro name:", "placeholder": ""}),
        ("show",   "Show macro",   {"label": "Macro name:", "placeholder": ""}),
    ],
    "fingerprint": [
        ("sync",        "Sync fingerprints", {}),
        ("cache",       "View cache",        {}),
        ("clear-cache", "Clear cache",       {}),
    ],
    "lock": [
        ("list",    "List locks",    {}),
        ("claim",   "Claim lock",    {"label": "File path:", "placeholder": ""}),
        ("release", "Release lock",  {"label": "File path:", "placeholder": ""}),
    ],
    "agent": [
        ("list",       "List agents",    {}),
        ("register",   "Register agent", {"label": "Agent name:", "placeholder": ""}),
        ("locks",      "View locks",     {}),
    ],
    "retrieval": [
        ("status", "Retrieval status", {}),
        ("test",   "Test retrieval",   {"label": "Search query:", "placeholder": "e.g. authentication"}),
    ],
    "prove": [
        ("run",       "Run value proof",  {}),
        ("list",      "List scenarios",   {}),
        ("providers", "Show providers",   {}),
    ],
    "alerts": [
        ("test --channel webhook", "Test webhook", {}),
        ("test --channel slack",   "Test Slack",   {}),
    ],
    "fleet": [
        ("", "Show fleet status", {}),
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
        _exec(cmd, value)
        return

    # Check if command needs a subcommand picked
    if cmd in _SUBCOMMAND_COMMANDS:
        subs = _SUBCOMMAND_COMMANDS[cmd]
        label = _POLISHED_LABELS.get(cmd, cmd)
        display_options = [(sub_args, display) for sub_args, display, _ in subs]
        choice = pick(
            paint(label, Color.PASTEL_YELLOW, c),
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
            _exec(cmd, f"{choice} {value}")
        else:
            _exec(cmd, choice)
        return

    # No special handling needed — run directly
    _exec(cmd)


def _header_compact() -> str:
    c = supports_color()
    token = paint("token", Color.WHITE + Color.BOLD, c)
    pak = paint("pak", Color.TEAL + Color.BOLD, c)
    return f"\U0001F4E6 {token}{pak}"


def _wait() -> None:
    c = supports_color()
    sys.stdout.write(f"\n  {paint('Press any key to return...', Color.LIGHT_GRAY, c)}")
    sys.stdout.flush()
    try:
        getch()
    except (PickerUnavailable, KeyboardInterrupt, EOFError):
        pass


def _result_screen(header: str, title: str, message: str,
                   next_actions: list[tuple[str, str]]) -> Optional[str]:
    """Show result with next action suggestions. Returns selected action value or None."""
    c = supports_color()
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.write(f"{header}\n\n")
    sys.stdout.write(f"  {paint(title, Color.PASTEL_YELLOW, c)}\n\n")
    sys.stdout.write(f"  {message}\n\n")

    if next_actions:
        next_actions.append((_BACK_SENTINEL, paint("Back", Color.LIGHT_GRAY, c)))
        return pick(
            paint("Next:", Color.LIGHT_GRAY, c),
            next_actions,
            header="",
        )
    _wait()
    return None


# ---------------------------------------------------------------------------
# Home menu items
# ---------------------------------------------------------------------------

_HOME_ITEMS = [
    ("start_proxy",  "Start proxy"),
    ("run_demo",     "Run demo"),
    ("check_health", "Proxy status"),
    ("view_spend",   "Spend & savings"),
    ("configure",    "Configure"),
    ("companion",    "Companion"),
    ("diagnose",     "Troubleshoot"),
    ("browse_all",   "Browse all commands"),
]

_SEARCH_ALIASES: dict[str, list[str]] = {
    "start_proxy":  ["start", "proxy", "run", "launch", "serve"],
    "run_demo":     ["demo", "sample", "example", "test compression"],
    "check_health": ["status", "health", "ping", "alive", "proxy status"],
    "view_spend":   ["cost", "spend", "savings", "budget", "money", "price", "usage"],
    "configure":    ["config", "settings", "setup", "edit", "route", "recipe", "budget"],
    "companion":    ["claude", "codex", "session", "capsule", "journal", "mcp", "companion"],
    "diagnose":     ["doctor", "diag", "fix", "repair", "debug", "troubleshoot", "health check"],
    "browse_all":   ["all", "commands", "search", "find", "list"],
}


# ---------------------------------------------------------------------------
# Section menus
# ---------------------------------------------------------------------------

def _section_demo(hdr: str) -> None:
    c = supports_color()
    while True:
        choice = pick(
            paint("Run demo", Color.PASTEL_YELLOW, c),
            [
                ("",                      "Run default demo"),
                ("--category python",     "Python sample"),
                ("--category javascript", "JavaScript sample"),
                ("--category markdown",   "Markdown sample"),
                ("--category config",     "Config sample"),
                ("--seed",                "Seed demo data"),
                ("--clear",               "Reset demo data"),
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
            ans = pick("Reset demo data?", confirm_opts, header=hdr,
                       subtitle="This will remove current demo artifacts and recreate defaults.")
            if ans != "yes":
                continue
        _exec("demo", choice)
        _wait()


def _section_spend(hdr: str) -> None:
    c = supports_color()
    while True:
        choice = pick(
            paint("Spend & savings", Color.PASTEL_YELLOW, c),
            [
                ("",             "Today"),
                ("--week",       "This week"),
                ("--month",      "This month"),
                ("--by-model",   "By model"),
                ("--export-csv", "Export CSV"),
            ],
            header=hdr,
            subtitle="View usage, spend, and estimated savings.",
            back_label="Back",
        )
        if choice is None or choice == _BACK_SENTINEL:
            return
        _exec("cost", choice)
        _wait()


def _section_configure(hdr: str) -> None:
    c = supports_color()
    while True:
        choice = pick(
            paint("Configure", Color.PASTEL_YELLOW, c),
            [
                ("show",     "View current config"),
                ("validate", "Validate config"),
                ("init",     "Create default config"),
                ("migrate",  "Migrate legacy config"),
                ("path",     "Show config file path"),
            ],
            header=hdr,
            subtitle="View, validate, or change your configuration.",
            back_label="Back",
        )
        if choice is None or choice == _BACK_SENTINEL:
            return
        _exec("config", choice)
        _wait()


def _section_companion(hdr: str) -> None:
    c = supports_color()
    while True:
        choice = pick(
            paint("Companion", Color.PASTEL_YELLOW, c),
            [
                ("claude",       "Launch Claude companion"),
                ("codex",        "Launch Codex companion"),
            ],
            header=hdr,
            subtitle="Launch AI coding tools with tokenpak optimization active.",
            back_label="Back",
        )
        if choice is None or choice == _BACK_SENTINEL:
            return
        _exec(choice)
        _wait()


def _section_diagnose(hdr: str) -> None:
    c = supports_color()
    while True:
        choice = pick(
            paint("Troubleshoot", Color.PASTEL_YELLOW, c),
            [
                ("",              "Run diagnostics"),
                ("--fix",         "Diagnose and auto-fix"),
                ("--verbose",     "Verbose diagnostics"),
                ("--claude-code", "Check companion setup"),
            ],
            header=hdr,
            subtitle="Find and fix issues with your tokenpak setup.",
            back_label="Back",
        )
        if choice is None or choice == _BACK_SENTINEL:
            return
        _exec("doctor", choice)
        _wait()


_POLISHED_LABELS: dict[str, str] = {
    "start":        "Start proxy",
    "stop":         "Stop proxy",
    "restart":      "Restart proxy",
    "demo":         "Run demo",
    "cost":         "View spend & savings",
    "status":       "Proxy status",
    "logs":         "Recent logs",
    "index":        "Index directory",
    "search":       "Search indexed content",
    "route":        "Manage routing rules",
    "recipe":       "Manage compression recipes",
    "template":     "Manage prompt templates",
    "budget":       "Set budget limits",
    "alerts":       "Manage alert channels",
    "goals":        "Track savings goals",
    "config":       "View & edit config",
    "explain":      "Explain workflow profiles",
    "version":      "Show version",
    "update":       "Update tokenpak",
    "benchmark":    "Run benchmarks",
    "calibrate":    "Calibrate workers",
    "doctor":       "Run diagnostics",
    "diagnose":     "Full health check",
    "dashboard":    "Live dashboard",
    "timeline":     "View savings trend",
    "attribution":  "Savings by source",
    "models":       "Per-model breakdown",
    "forecast":     "Cost projections",
    "debug":        "Toggle debug logging",
    "claude":       "Launch Claude companion",
    "codex":        "Launch Codex companion",
    "test":         "Interactive A/B test",
    "prove":        "A/B value proof",
    "trigger":      "Manage event triggers",
    "macro":        "Manage macros",
    "config-check": "Validate config file",
    "validate":     "Validate JSON file",
    "diff":         "Show context changes",
    "serve":        "Start proxy server",
    "replay":       "Replay captured sessions",
    "audit":        "Audit log management",
    "compliance":   "Compliance reports",
    # Internal / advanced — kept but separated
    "fingerprint":  "Fingerprint management",
    "agent":        "Agent coordination",
    "lock":         "File lock management",
    "run":          "Schedule macro runs",
    "learn":        "View learned patterns",
    "vault-health": "Vault index health",
    "fleet":        "Fleet status",
    "aggregate":    "Aggregate ledger",
    "requests":     "Live request explorer",
    "stats":        "Registry stats",
    "retrieval":    "Test search retrieval",
    "monitor":      "Start live monitor",
}

# Commands shown in the default "Common" view
_COMMON_COMMANDS = {
    "start", "stop", "restart", "demo", "cost", "status", "logs",
    "index", "search", "route", "recipe", "budget", "config", "explain",
    "version", "update", "doctor", "diagnose", "dashboard", "timeline",
    "models", "forecast", "claude", "codex", "test", "prove",
    "benchmark", "calibrate", "alerts", "template", "goals",
    "attribution", "debug",
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
        _wait()


# ---------------------------------------------------------------------------
# Home screen — immediate-execute commands
# ---------------------------------------------------------------------------

_IMMEDIATE = {"start_proxy", "check_health"}


def _handle_home_item(item: str, hdr: str) -> None:
    """Dispatch a home menu item."""
    if item == "start_proxy":
        _exec("start")
        _wait()
    elif item == "run_demo":
        _section_demo(hdr)
    elif item == "check_health":
        _exec("status")
        _wait()
    elif item == "view_spend":
        _section_spend(hdr)
    elif item == "configure":
        _section_configure(hdr)
    elif item == "companion":
        _section_companion(hdr)
    elif item == "diagnose":
        _section_diagnose(hdr)
    elif item == "browse_all":
        _section_browse_all(hdr)


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

def run_menu() -> None:
    """Launch the interactive branded menu."""
    try:
        hdr = _header()

        while True:
            # Build home screen with status strip
            status = _status_strip()

            c = supports_color()
            home_options = []
            for val, label in _HOME_ITEMS:
                home_options.append((val, label))

            # Show CLI command hint on the right for each item
            _CMD_HINTS = {
                "start_proxy":  "tokenpak start",
                "run_demo":     "tokenpak demo",
                "check_health": "tokenpak status",
                "view_spend":   "tokenpak cost",
                "configure":    "tokenpak config",
                "companion":    "",
                "diagnose":     "tokenpak doctor",
                "browse_all":   "",
            }
            styled_options = []
            for val, label in home_options:
                hint = _CMD_HINTS.get(val, "")
                if hint:
                    styled = (
                        f"{label:<26}"
                        + paint(hint, Color.LIGHT_GRAY, c)
                    )
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

    except PickerUnavailable:
        print("Interactive menu requires a terminal.")
        print("Run `tokenpak help` for a non-interactive command list.")
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
