# SPDX-License-Identifier: Apache-2.0
"""Interactive branded command menu — ``tokenpak`` / ``tokenpak menu``.

Full arrow-key navigable menu with:
- Brand colors: teal accent, pastel yellow, light gray, white
- Category -> command -> detail/options -> execute flow
- Text input prompts for commands that need values
- No CLI syntax knowledge required
"""

from __future__ import annotations

import sys
from typing import Optional

from tokenpak._formatting.colors import Color, paint, supports_color
from tokenpak._formatting.picker import (
    PickerUnavailable,
    _BACK_SENTINEL,
    confirm,
    getch,
    pick,
    prompt_input,
)


# ---------------------------------------------------------------------------
# Brand header
# ---------------------------------------------------------------------------

def _branded_header() -> str:
    """Lowercase 'tokenpak' — 'token' in white, 'pak' in teal."""
    try:
        from tokenpak import __version__
    except ImportError:
        __version__ = "?"

    c = supports_color()
    token = paint("token", Color.WHITE + Color.BOLD, c)
    pak = paint("pak", Color.TEAL + Color.BOLD, c)
    ver = paint(f"v{__version__}", Color.LIGHT_GRAY, c)
    tagline = paint("LLM Proxy with Context Compression", Color.LIGHT_GRAY, c)

    return f"\n  {token}{pak}  {ver}\n  {tagline}\n"


# ---------------------------------------------------------------------------
# Command detail configs
# ---------------------------------------------------------------------------

# Each command maps to a dict describing how to interact with it.
#   "detail"  — longer description shown on the command detail screen
#   "type"    — "run" | "flags" | "subcommands" | "input"
#   "options" — list of (flag_string, label) for flags/subcommands
#   "input_label" — prompt text for input-type commands

_COMMAND_CONFIGS: dict[str, dict] = {
    # ── Getting Started ──
    "start": {
        "detail": "Start the tokenpak proxy on your machine. Routes API requests through compression and cost tracking.",
        "type": "flags",
        "options": [
            ("", "Start with defaults"),
            ("--port 8766", "Custom port"),
            ("--workers 4", "Multi-core (4 workers)"),
            ("--safe", "Safe mode (no compression)"),
        ],
    },
    "stop": {
        "detail": "Stop the running tokenpak proxy.",
        "type": "run",
    },
    "restart": {
        "detail": "Restart the proxy with current configuration.",
        "type": "run",
    },
    "demo": {
        "detail": "See compression in action on sample data. No API key needed.",
        "type": "flags",
        "options": [
            ("", "Run default demo"),
            ("--category python", "Python code samples"),
            ("--category javascript", "JavaScript samples"),
            ("--category markdown", "Markdown documents"),
            ("--category config", "Config files"),
            ("--seed", "Seed demo data"),
            ("--clear", "Clear demo data"),
        ],
    },
    "cost": {
        "detail": "View how much you've spent on API calls. Tracks per-model, per-session costs.",
        "type": "flags",
        "options": [
            ("", "Today's spend"),
            ("--week", "This week's spend"),
            ("--month", "This month's spend"),
            ("--by-model", "Breakdown by model"),
            ("--export-csv", "Export to CSV"),
        ],
    },
    "status": {
        "detail": "Check proxy health, compression stats, and savings summary at a glance.",
        "type": "flags",
        "options": [
            ("", "Quick status"),
            ("--full", "Full detail"),
            ("--by-model", "By model breakdown"),
            ("--json", "JSON output"),
        ],
    },
    "logs": {
        "detail": "Show recent proxy log output.",
        "type": "flags",
        "options": [
            ("", "Last 50 lines"),
            ("-n 100", "Last 100 lines"),
            ("-n 500", "Last 500 lines"),
        ],
    },
    # ── Indexing ──
    "index": {
        "detail": "Index a directory so tokenpak can inject relevant context into your prompts automatically.",
        "type": "input",
        "input_label": "Directory path to index:",
        "input_placeholder": "e.g. ~/projects/myapp",
    },
    "search": {
        "detail": "Search your indexed content using hybrid BM25 + semantic retrieval.",
        "type": "input",
        "input_label": "Search query:",
        "input_placeholder": "e.g. authentication middleware",
    },
    # ── Configuration ──
    "route": {
        "detail": "Manage model routing rules. Route requests to different providers based on model, token count, or prefix.",
        "type": "subcommands",
        "options": [
            ("list", "View all routing rules"),
            ("add", "Add a new routing rule"),
            ("test", "Test which rule matches"),
        ],
    },
    "recipe": {
        "detail": "Manage compression recipes. Recipes define how different content types are compressed.",
        "type": "subcommands",
        "options": [
            ("create", "Create a new recipe"),
            ("validate", "Validate recipe YAML"),
            ("test", "Test recipe on input"),
            ("benchmark", "Benchmark recipe speed"),
        ],
    },
    "template": {
        "detail": "Manage prompt templates for reusable, optimized prompts.",
        "type": "run",
    },
    "budget": {
        "detail": "Set daily or monthly API spend limits. Hard-stops requests when budget is exceeded.",
        "type": "subcommands",
        "options": [
            ("status", "View current budget"),
            ("set", "Set budget limits"),
            ("history", "View spend history"),
        ],
    },
    "alerts": {
        "detail": "Configure and test alert delivery channels (webhook, Slack).",
        "type": "subcommands",
        "options": [
            ("test --channel webhook", "Test webhook alert"),
            ("test --channel slack", "Test Slack alert"),
        ],
    },
    "goals": {
        "detail": "Set and track savings goals to measure tokenpak's impact.",
        "type": "run",
    },
    "config": {
        "detail": "View, edit, validate, and sync your tokenpak configuration.",
        "type": "subcommands",
        "options": [
            ("show", "View current config"),
            ("validate", "Validate config file"),
            ("init", "Create default config"),
            ("migrate", "Migrate legacy config"),
            ("path", "Show config file path"),
        ],
    },
    "explain": {
        "detail": "Explain what each workflow profile does (safe, balanced, aggressive, agentic).",
        "type": "run",
    },
    # ── Versioning ──
    "version": {
        "detail": "Show tokenpak version information.",
        "type": "run",
    },
    "update": {
        "detail": "Update tokenpak to the latest version.",
        "type": "run",
    },
    # ── Operations ──
    "benchmark": {
        "detail": "Run compression benchmarks to measure performance on your workloads.",
        "type": "run",
    },
    "calibrate": {
        "detail": "Auto-detect optimal worker count for your machine's CPU and memory.",
        "type": "input",
        "input_label": "Directory to calibrate against:",
        "input_placeholder": "e.g. ~/projects/myapp",
    },
    "doctor": {
        "detail": "Run comprehensive diagnostics and optionally auto-fix issues.",
        "type": "flags",
        "options": [
            ("", "Run diagnostics"),
            ("--fix", "Diagnose and auto-fix"),
            ("--verbose", "Verbose output"),
            ("--claude-code", "Check Claude Code setup"),
        ],
    },
    "diagnose": {
        "detail": "Full health check across config, vault, cache, proxy, and disk.",
        "type": "flags",
        "options": [
            ("", "Run health check"),
            ("--verbose", "Verbose output"),
            ("--json", "JSON output"),
        ],
    },
    "dashboard": {
        "detail": "Open a live real-time dashboard showing proxy health, requests, and savings.",
        "type": "flags",
        "options": [
            ("", "Local dashboard"),
            ("--fleet", "Fleet-wide view"),
        ],
    },
    "timeline": {
        "detail": "View your savings trend over time as a chart.",
        "type": "flags",
        "options": [
            ("", "Last 7 days"),
            ("--days 30", "Last 30 days"),
            ("--chart", "With chart"),
        ],
    },
    "attribution": {
        "detail": "See savings broken down by agent, skill, or model.",
        "type": "run",
    },
    "models": {
        "detail": "View per-model usage efficiency and cost breakdown.",
        "type": "run",
    },
    "forecast": {
        "detail": "Project your cost burn rate and when you'll hit budget limits.",
        "type": "flags",
        "options": [
            ("--period 7d", "7-day forecast"),
            ("--period 30d", "30-day forecast"),
            ("--period 90d", "90-day forecast"),
        ],
    },
    "debug": {
        "detail": "Toggle verbose debug logging on or off.",
        "type": "subcommands",
        "options": [
            ("on", "Enable debug logging"),
            ("off", "Disable debug logging"),
        ],
    },
    "learn": {
        "detail": "View or reset patterns tokenpak has learned from your usage.",
        "type": "run",
    },
    "vault-health": {
        "detail": "Check vault index integrity and repair if needed.",
        "type": "run",
    },
    "fleet": {
        "detail": "View status of all machines in your proxy fleet.",
        "type": "run",
    },
    "aggregate": {
        "detail": "Aggregate request data across multiple machines.",
        "type": "run",
    },
    "requests": {
        "detail": "Browse recent API requests in real-time.",
        "type": "run",
    },
    # ── Companion ──
    "claude": {
        "detail": "Launch Claude Code with the tokenpak companion active for budget tracking and context optimization.",
        "type": "run",
    },
    "codex": {
        "detail": "Launch Codex with the tokenpak companion active.",
        "type": "run",
    },
    "test": {
        "detail": "Run an interactive A/B test comparing direct API calls vs tokenpak-optimized calls.",
        "type": "run",
    },
    "prove": {
        "detail": "Run scriptable A/B value proof tests across models and scenarios.",
        "type": "run",
    },
    # ── Advanced ──
    "trigger": {
        "detail": "Manage event-driven triggers that fire actions on specific conditions.",
        "type": "run",
    },
    "macro": {
        "detail": "Create and manage reusable macro sequences.",
        "type": "run",
    },
    "fingerprint": {
        "detail": "Manage content fingerprints used for change detection and cache invalidation.",
        "type": "run",
    },
    "agent": {
        "detail": "Coordinate multi-agent workflows with locks and registries.",
        "type": "run",
    },
    "lock": {
        "detail": "Manage file locks for safe concurrent access.",
        "type": "run",
    },
    "run": {
        "detail": "Schedule and execute macro runs.",
        "type": "run",
    },
    "replay": {
        "detail": "Inspect and re-run previously captured API sessions.",
        "type": "run",
    },
    "audit": {
        "detail": "View and manage the enterprise audit log.",
        "type": "run",
    },
    "compliance": {
        "detail": "Generate compliance reports for your API usage.",
        "type": "run",
    },
    "validate": {
        "detail": "Validate a tokenpak JSON file against the schema.",
        "type": "run",
    },
    "config-check": {
        "detail": "Validate your proxy configuration file for errors.",
        "type": "run",
    },
    "diff": {
        "detail": "Show what changed in context between requests.",
        "type": "run",
    },
    "stats": {
        "detail": "Show registry and cache statistics.",
        "type": "run",
    },
    "serve": {
        "detail": "Start the proxy server directly (low-level alternative to 'start').",
        "type": "flags",
        "options": [
            ("", "Start with defaults"),
            ("--port 8766", "Custom port"),
            ("--safe", "Safe mode"),
            ("--telemetry", "Enable telemetry server"),
        ],
    },
    "retrieval": {
        "detail": "Inspect and test the hybrid BM25 + vector search retrieval system.",
        "type": "run",
    },
}


# ---------------------------------------------------------------------------
# Menu helpers
# ---------------------------------------------------------------------------

def _build_category_options() -> list[tuple[str, str]]:
    from tokenpak._cli_core import _COMMAND_GROUPS

    c = supports_color()
    options = []
    for group_name, cmds in _COMMAND_GROUPS.items():
        count = len(cmds)
        name = paint(group_name, Color.PASTEL_YELLOW, c)
        cnt = paint(f"{count} commands", Color.LIGHT_GRAY, c)
        options.append((group_name, f"{name}  {cnt}"))
    return options


def _build_command_options(group_name: str) -> list[tuple[str, str]]:
    from tokenpak._cli_core import _COMMAND_GROUPS

    c = supports_color()
    cmds = _COMMAND_GROUPS.get(group_name, [])
    options = []
    for cmd, desc in cmds:
        cmd_styled = paint(f"{cmd:<16}", Color.WHITE, c)
        desc_styled = paint(desc, Color.LIGHT_GRAY, c)
        options.append((cmd, f"{cmd_styled}{desc_styled}"))
    return options


def _execute_command(cmd_name: str, extra_args: str = "") -> None:
    """Execute a CLI command, optionally with extra arguments."""
    full_cmd = f"tokenpak {cmd_name}" + (f" {extra_args}" if extra_args else "")

    c = supports_color()
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.write(f"\n  {paint('Running:', Color.LIGHT_GRAY, c)} {paint(full_cmd, Color.TEAL, c)}\n")
    sys.stdout.write(f"  {paint(chr(0x2500) * 40, Color.LIGHT_GRAY, c)}\n\n")
    sys.stdout.write("\033[?25h")
    sys.stdout.flush()

    original_argv = sys.argv[:]
    try:
        argv = ["tokenpak", cmd_name]
        if extra_args:
            argv.extend(extra_args.split())
        sys.argv = argv
        from tokenpak._cli_core import main as cli_main
        cli_main()
    except SystemExit:
        pass
    except Exception as exc:
        print(f"\n  {paint('Error:', Color.RED, c)} {exc}")
    finally:
        sys.argv = original_argv


def _wait_for_key() -> None:
    c = supports_color()
    sys.stdout.write(f"\n  {paint('Press any key to return...', Color.LIGHT_GRAY, c)}")
    sys.stdout.flush()
    try:
        getch()
    except (PickerUnavailable, KeyboardInterrupt, EOFError):
        pass


def _show_command_detail(cmd_name: str, header: str) -> Optional[str]:
    """Show command detail screen with executable options. Returns None to go back."""
    from tokenpak._cli_core import _COMMAND_GROUPS

    c = supports_color()
    cfg = _COMMAND_CONFIGS.get(cmd_name, {"detail": "", "type": "run"})
    detail = cfg.get("detail", "")
    cmd_type = cfg.get("type", "run")

    # ── Type: run (no options needed) ──
    if cmd_type == "run":
        # Show detail, offer to run or go back
        options = [
            ("run", paint("Run", Color.TEAL, c) + "  " + paint(f"tokenpak {cmd_name}", Color.LIGHT_GRAY, c)),
            (_BACK_SENTINEL, paint("< Back", Color.LIGHT_GRAY, c)),
        ]
        cmd_title = paint(cmd_name, Color.TEAL + Color.BOLD, c)
        subtitle = detail if detail else ""
        choice = pick(cmd_title, options, header=header, subtitle=subtitle)
        if choice == "run":
            _execute_command(cmd_name)
            _wait_for_key()
        return None

    # ── Type: flags (pick one flag set to run with) ──
    if cmd_type == "flags":
        flag_options = cfg.get("options", [])
        items = []
        for flag_str, label in flag_options:
            items.append((flag_str, label))
        items.append((_BACK_SENTINEL, paint("< Back", Color.LIGHT_GRAY, c)))

        cmd_title = paint(cmd_name, Color.TEAL + Color.BOLD, c)
        choice = pick(cmd_title, items, header=header, subtitle=detail, back_label=None)
        if choice is None or choice == _BACK_SENTINEL:
            return None
        _execute_command(cmd_name, choice)
        _wait_for_key()
        return None

    # ── Type: subcommands ──
    if cmd_type == "subcommands":
        sub_options = cfg.get("options", [])
        items = [(sub, label) for sub, label in sub_options]
        items.append((_BACK_SENTINEL, paint("< Back", Color.LIGHT_GRAY, c)))

        cmd_title = paint(cmd_name, Color.TEAL + Color.BOLD, c)
        choice = pick(cmd_title, items, header=header, subtitle=detail)
        if choice is None or choice == _BACK_SENTINEL:
            return None
        _execute_command(cmd_name, choice)
        _wait_for_key()
        return None

    # ── Type: input (needs text before running) ──
    if cmd_type == "input":
        input_label = cfg.get("input_label", f"Enter value for {cmd_name}:")
        placeholder = cfg.get("input_placeholder", "")
        value = prompt_input(input_label, header=header, placeholder=placeholder)
        if value:
            _execute_command(cmd_name, value)
            _wait_for_key()
        return None

    # Fallback
    _execute_command(cmd_name)
    _wait_for_key()
    return None


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

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
            )

            if choice is None:
                break

            # ── Screen 2: Command picker within category ──
            while True:
                cmd_options = _build_command_options(choice)
                selected = pick(
                    paint(f"{choice}:", Color.PASTEL_YELLOW, supports_color()),
                    cmd_options,
                    header=header,
                    subtitle="Select a command",
                    back_label="Back",
                    filterable=True,
                )

                if selected is None or selected == _BACK_SENTINEL:
                    break

                # ── Screen 3: Command detail ──
                _show_command_detail(selected, header)

    except PickerUnavailable:
        print("Interactive menu requires a terminal.")
        print("Run `tokenpak help` for a non-interactive command list.")
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
