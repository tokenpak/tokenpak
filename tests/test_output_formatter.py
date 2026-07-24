import pytest

pytest.importorskip(
    "tokenpak._formatting.formatter", reason="module not available in current build"
)
import types

from tokenpak import cli
from tokenpak._formatting import symbols as FS
from tokenpak._formatting.formatter import OutputFormatter
from tokenpak._formatting.modes import OutputMode, resolve_mode


def test_symbols_are_semantic_set():
    assert FS.ENABLED == "●"
    assert FS.DISABLED == "○"
    assert FS.OPTIMIZED == "▲"
    assert FS.REDUCED == "▼"
    assert FS.WARNING == "⚠"
    assert FS.ERROR == "✖"
    assert FS.SUCCESS == "✓"


def test_formatter_header_and_divider():
    import tokenpak

    f = OutputFormatter("Status")
    out = f.header()
    # Banner must reflect the live package version, not a hardcoded string.
    assert f"TOKENPAK v{tokenpak.__version__}  |  Status" in out
    assert "─" * 40 in out


def test_kv_alignment_contains_colon_rows():
    f = OutputFormatter("Usage")
    out = f.kv([("Requests", "10"), ("Tokens", "200")])
    assert "Requests" in out and "Tokens" in out
    assert " : " in out


def test_mode_resolution_defaults_to_normal():
    args = types.SimpleNamespace(output="bad-mode")
    assert resolve_mode(args) == OutputMode.NORMAL


def test_parser_has_usage_and_savings_commands():
    parser = cli.build_parser()
    args = parser.parse_args(["usage", "--days", "7"])
    assert args.command == "usage"
    assert args.days == 7
    args2 = parser.parse_args(["savings", "--days", "14"])
    assert args2.command == "savings"
    assert args2.days == 14


def test_minimal_line_format_single_line():
    f = OutputFormatter("Savings", minimal=True)
    out = f.minimal_line(["Enabled", "Balanced", "41%", "20k target", "$0.11 avg"])
    assert out == "Enabled | Balanced | 41% | 20k target | $0.11 avg"
