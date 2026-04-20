#!/usr/bin/env python3
"""Auto-generate docs/cli-reference.md from tokenpak/cli.py argparse definitions.

This file is auto-generated. Edit tokenpak/cli.py and re-run:
    python scripts/generate-cli-docs.py

Usage:
    python scripts/generate-cli-docs.py            # writes docs/cli-reference.md
    python scripts/generate-cli-docs.py --stdout   # print to stdout instead
"""

import argparse
import sys
import types
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Locate repo root (this script lives in <repo>/scripts/)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
TOKENPAK_PKG = REPO_ROOT / "tokenpak"
OUTPUT_PATH = REPO_ROOT / "docs" / "cli-reference.md"

# ---------------------------------------------------------------------------
# Mock all tokenpak sub-modules that have side-effects at import time.
# build_parser() only uses argparse; the imported symbols are only referenced
# inside cmd_* handler bodies which we never call.
# ---------------------------------------------------------------------------

def _make_mock_module(name: str, **attrs):
    """Return a minimal module object with the given attributes."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


def _install_mocks():
    """Pre-populate sys.modules so that tokenpak relative imports resolve cleanly."""
    # A sentinel class so that isinstance checks on mocked types don't crash
    class _Stub:
        def __init__(self, *a, **kw):
            pass
        def __call__(self, *a, **kw):
            return self
        def __getattr__(self, name):
            return self
        def __iter__(self):
            return iter([])
        def __bool__(self):
            return False

    stub = _Stub()

    mocks = {
        "tokenpak.formatting": _make_mock_module(
            "tokenpak.formatting",
            OutputFormatter=_Stub,
            OutputMode=_Stub(),
            resolve_mode=lambda *a, **kw: None,
            symbols=_Stub(),
        ),
        "tokenpak.budget": _make_mock_module(
            "tokenpak.budget",
            BudgetBlock=_Stub,
            quadratic_allocate=stub,
        ),
        "tokenpak.calibration": _make_mock_module(
            "tokenpak.calibration",
            calibrate_workers=stub,
            get_recommended_workers=stub,
        ),
        "tokenpak.miss_detector": _make_mock_module(
            "tokenpak.miss_detector",
            DEFAULT_GAPS_PATH=str(Path.home() / ".tokenpak" / "gaps.jsonl"),
            should_expand_retrieval=stub,
        ),
        "tokenpak.processors": _make_mock_module(
            "tokenpak.processors",
            get_processor=stub,
        ),
        "tokenpak.registry": _make_mock_module(
            "tokenpak.registry",
            Block=_Stub,
            BlockRegistry=_Stub,
        ),
        "tokenpak.security": _make_mock_module(
            "tokenpak.security",
            secure_write_config=stub,
        ),
        "tokenpak.tokens": _make_mock_module(
            "tokenpak.tokens",
            cache_info=stub,
            count_tokens=stub,
            truncate_to_tokens=stub,
        ),
        "tokenpak.walker": _make_mock_module(
            "tokenpak.walker",
            walk_directory=stub,
        ),
        "tokenpak.wire": _make_mock_module(
            "tokenpak.wire",
            pack=stub,
        ),
    }

    for name, mod in mocks.items():
        sys.modules[name] = mod

    # Also mock the top-level tokenpak package so it's importable
    if "tokenpak" not in sys.modules:
        pkg = types.ModuleType("tokenpak")
        pkg.__path__ = [str(TOKENPAK_PKG)]
        pkg.__package__ = "tokenpak"
        sys.modules["tokenpak"] = pkg


# ---------------------------------------------------------------------------
# Argparse walker
# ---------------------------------------------------------------------------

def _get_subparser_map(parser: argparse.ArgumentParser) -> Optional[dict]:
    """Return {name: subparser} or None if no subparsers defined."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return None


def _format_action(action: argparse.Action) -> Optional[str]:
    """Format a single argument action as a flag-description line."""
    # Skip internal / positional-only noise
    if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
        return None

    flags = ", ".join(f"`{f}`" for f in action.option_strings) if action.option_strings else None
    if flags is None:
        # Positional argument
        metavar = action.metavar or action.dest.upper()
        flags = f"`{metavar}`"

    # Build description
    desc = action.help or ""
    # Append default if non-trivial
    if action.default not in (None, argparse.SUPPRESS, False, True):
        desc = f"{desc} (default: {action.default})" if desc else f"default: {action.default}"

    # Append choices
    if action.choices:
        choices_str = ", ".join(f"`{c}`" for c in action.choices)
        desc = f"{desc} — choices: {choices_str}" if desc else f"choices: {choices_str}"

    return f"- {flags} — {desc}" if desc else f"- {flags}"


def _render_command(
    name: str,
    parser: argparse.ArgumentParser,
    group_name: str,
    lines: List[str],
    depth: int = 3,
) -> None:
    """Render a command (and its subcommands) into the lines list."""
    heading = "#" * depth
    desc = parser.description or (parser._defaults.get("func", None) and
                                   getattr(parser._defaults["func"], "__doc__", None)) or ""
    # Fall back to the parser's help stored in its parent's choices
    if not desc:
        desc = ""

    lines.append(f"{heading} `tokenpak {name}`")
    if desc:
        lines.append("")
        lines.append(desc.strip())
    lines.append("")

    # Collect non-subparser actions
    flag_lines = []
    for action in parser._actions:
        line = _format_action(action)
        if line:
            flag_lines.append(line)

    if flag_lines:
        lines.append("**Flags:**")
        lines.append("")
        lines.extend(flag_lines)
        lines.append("")

    # Subcommands
    submap = _get_subparser_map(parser)
    if submap:
        lines.append("**Subcommands:**")
        lines.append("")
        for sub_name, sub_parser in submap.items():
            sub_desc = sub_parser.description or ""
            if not sub_desc:
                # Try to get from parent's help (stored during add_parser)
                sub_desc = getattr(sub_parser, "_help_text", "") or ""
            flag_parts = []
            for action in sub_parser._actions:
                if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
                    continue
                if action.option_strings:
                    flag_parts.append(action.option_strings[0])
                else:
                    flag_parts.append(action.dest.upper())

            usage_flags = " ".join(flag_parts)
            line = f"- `{sub_name}`"
            if sub_desc:
                line += f" — {sub_desc}"
            lines.append(line)

            # Detailed flags for each subcommand
            sub_flag_lines = []
            for action in sub_parser._actions:
                fl = _format_action(action)
                if fl:
                    sub_flag_lines.append(f"  {fl}")
            if sub_flag_lines:
                lines.extend(sub_flag_lines)

        lines.append("")


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate(stdout_only: bool = False) -> str:
    _install_mocks()

    # Add repo root to path so `import tokenpak.cli` resolves
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    import importlib
    # Implementation lives at tokenpak.cli._impl; the public tokenpak.cli
    # package re-exports only `main`, not build_parser()/_COMMAND_GROUPS.
    cli_mod = importlib.import_module("tokenpak.cli._impl")

    parser = cli_mod.build_parser()
    command_groups = cli_mod._COMMAND_GROUPS

    # Build a map of all subparsers keyed by command name
    submap = _get_subparser_map(parser) or {}

    lines = [
        "# CLI Reference",
        "",
        "_Auto-generated from `tokenpak/cli.py` — do not edit by hand._",
        "_To update: edit `tokenpak/cli.py` then run `python scripts/generate-cli-docs.py`._",
        "",
        "---",
        "",
    ]

    # Emit commands in _COMMAND_GROUPS order
    documented = set()

    for group_name, commands in command_groups.items():
        lines.append(f"## Group: {group_name}")
        lines.append("")

        for cmd_name, cmd_desc in commands:
            documented.add(cmd_name)
            if cmd_name in submap:
                sub_parser = submap[cmd_name]
                # Use cmd_desc as description if parser has none
                if not sub_parser.description:
                    sub_parser.description = cmd_desc
                _render_command(cmd_name, sub_parser, group_name, lines)
            else:
                # Command exists in _COMMAND_GROUPS but has no parser registered
                lines.append(f"### `tokenpak {cmd_name}`")
                lines.append("")
                lines.append(cmd_desc)
                lines.append("")
                lines.append("_(custom args — see source)_")
                lines.append("")

        lines.append("---")
        lines.append("")

    # Catch any parsers not in _COMMAND_GROUPS (shouldn't happen, but be safe)
    extra = sorted(set(submap) - documented)
    if extra:
        lines.append("## Additional Commands")
        lines.append("")
        for cmd_name in extra:
            _render_command(cmd_name, submap[cmd_name], "Additional", lines)
        lines.append("---")
        lines.append("")

    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stdout", action="store_true", help="Print to stdout instead of writing file")
    args = ap.parse_args()

    content = generate(stdout_only=args.stdout)

    if args.stdout:
        sys.stdout.write(content)
    else:
        OUTPUT_PATH.write_text(content, encoding="utf-8")
        count = content.count("\n### `tokenpak ")
        print(f"Wrote {OUTPUT_PATH} ({count} top-level commands documented)")


if __name__ == "__main__":
    main()
