"""Unit tests for tokenpak/formatting/ module.

Covers:
  - formatting/colors.py  — Color constants, supports_color(), paint()
  - formatting/modes.py   — OutputMode enum, resolve_mode()
  - formatting/symbols.py — semantic symbol constants
  - formatting/formatter.py — OutputFormatter class (all methods)

No live API calls; all environment and stream dependencies are mocked.
"""

from __future__ import annotations

import json
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module imports
# ---------------------------------------------------------------------------
import tokenpak.compression.formatting.symbols as _symbols
from tokenpak.compression.formatting import OutputFormatter, OutputMode, resolve_mode
from tokenpak.compression.formatting.colors import Color, paint, supports_color

# ===========================================================================
# colors.py
# ===========================================================================

class TestColorConstants:
    """Color class holds expected ANSI escape codes."""

    def test_reset(self):
        assert Color.RESET == "\033[0m"

    def test_bold(self):
        assert Color.BOLD == "\033[1m"

    def test_cyan(self):
        assert Color.CYAN == "\033[36m"

    def test_green(self):
        assert Color.GREEN == "\033[32m"

    def test_yellow(self):
        assert Color.YELLOW == "\033[33m"

    def test_red(self):
        assert Color.RED == "\033[31m"

    def test_dim(self):
        assert Color.DIM == "\033[2m"


class TestSupportsColor:
    """supports_color() respects env vars and stream isatty."""

    def test_no_color_env_disables(self):
        with patch.dict("os.environ", {"NO_COLOR": "1"}, clear=False):
            assert supports_color() is False

    def test_tokenpak_no_color_disables(self):
        with patch.dict("os.environ", {"TOKENPAK_NO_COLOR": "1"}, clear=False):
            assert supports_color() is False

    def test_force_color_enables(self):
        # FORCE_COLOR overrides everything except NO_COLOR (NO_COLOR checked first)
        with patch.dict("os.environ", {"FORCE_COLOR": "1", "NO_COLOR": ""}, clear=False):
            # NO_COLOR="" is falsy — remove it to avoid interference
            env = {"FORCE_COLOR": "1"}
            with patch.dict("os.environ", env, clear=False):
                # strip NO_COLOR key if present
                import os
                old = os.environ.pop("NO_COLOR", None)
                old2 = os.environ.pop("TOKENPAK_NO_COLOR", None)
                try:
                    assert supports_color() is True
                finally:
                    if old is not None:
                        os.environ["NO_COLOR"] = old
                    if old2 is not None:
                        os.environ["TOKENPAK_NO_COLOR"] = old2

    def test_isatty_true_enables(self):
        stream = MagicMock()
        stream.isatty.return_value = True
        import os
        old_nc = os.environ.pop("NO_COLOR", None)
        old_tnc = os.environ.pop("TOKENPAK_NO_COLOR", None)
        old_fc = os.environ.pop("FORCE_COLOR", None)
        try:
            assert supports_color(stream) is True
        finally:
            if old_nc is not None:
                os.environ["NO_COLOR"] = old_nc
            if old_tnc is not None:
                os.environ["TOKENPAK_NO_COLOR"] = old_tnc
            if old_fc is not None:
                os.environ["FORCE_COLOR"] = old_fc

    def test_isatty_false_disables(self):
        stream = MagicMock()
        stream.isatty.return_value = False
        import os
        old_nc = os.environ.pop("NO_COLOR", None)
        old_tnc = os.environ.pop("TOKENPAK_NO_COLOR", None)
        old_fc = os.environ.pop("FORCE_COLOR", None)
        try:
            assert supports_color(stream) is False
        finally:
            if old_nc is not None:
                os.environ["NO_COLOR"] = old_nc
            if old_tnc is not None:
                os.environ["TOKENPAK_NO_COLOR"] = old_tnc
            if old_fc is not None:
                os.environ["FORCE_COLOR"] = old_fc

    def test_stream_without_isatty_attribute_disables(self):
        stream = object()  # no isatty attribute
        with patch.dict("os.environ", {}, clear=False):
            import os
            for k in ("NO_COLOR", "TOKENPAK_NO_COLOR", "FORCE_COLOR"):
                os.environ.pop(k, None)
            assert supports_color(stream) is False


class TestPaint:
    """paint() wraps/unwraps ANSI codes correctly."""

    def test_enabled_wraps_text(self):
        result = paint("hello", Color.RED, True)
        assert result == f"{Color.RED}hello{Color.RESET}"

    def test_disabled_returns_plain_text(self):
        result = paint("hello", Color.RED, False)
        assert result == "hello"

    def test_empty_string_enabled(self):
        result = paint("", Color.GREEN, True)
        assert result == f"{Color.GREEN}{Color.RESET}"

    def test_empty_string_disabled(self):
        assert paint("", Color.GREEN, False) == ""


# ===========================================================================
# modes.py
# ===========================================================================

class TestOutputMode:
    """OutputMode enum has correct string values and is a str subclass."""

    def test_normal_value(self):
        assert OutputMode.NORMAL == "normal"

    def test_verbose_value(self):
        assert OutputMode.VERBOSE == "verbose"

    def test_raw_value(self):
        assert OutputMode.RAW == "raw"

    def test_is_str_subclass(self):
        assert isinstance(OutputMode.NORMAL, str)

    def test_enum_membership(self):
        assert OutputMode("normal") is OutputMode.NORMAL
        assert OutputMode("verbose") is OutputMode.VERBOSE
        assert OutputMode("raw") is OutputMode.RAW


class TestResolveMode:
    """resolve_mode() converts args namespace to OutputMode with safe fallback."""

    def _args(self, output_val):
        ns = types.SimpleNamespace(output=output_val)
        return ns

    def test_normal(self):
        assert resolve_mode(self._args("normal")) is OutputMode.NORMAL

    def test_verbose(self):
        assert resolve_mode(self._args("verbose")) is OutputMode.VERBOSE

    def test_raw(self):
        assert resolve_mode(self._args("raw")) is OutputMode.RAW

    def test_invalid_falls_back_to_normal(self):
        assert resolve_mode(self._args("bogus")) is OutputMode.NORMAL

    def test_none_falls_back_to_normal(self):
        assert resolve_mode(self._args(None)) is OutputMode.NORMAL

    def test_empty_string_falls_back_to_normal(self):
        assert resolve_mode(self._args("")) is OutputMode.NORMAL

    def test_missing_output_attr_falls_back_to_normal(self):
        ns = types.SimpleNamespace()  # no output attr
        assert resolve_mode(ns) is OutputMode.NORMAL


# ===========================================================================
# symbols.py
# ===========================================================================

class TestSymbols:
    """Semantic symbol constants have expected Unicode characters."""

    def test_enabled(self):
        assert _symbols.ENABLED == "●"

    def test_disabled(self):
        assert _symbols.DISABLED == "○"

    def test_optimized(self):
        assert _symbols.OPTIMIZED == "▲"

    def test_reduced(self):
        assert _symbols.REDUCED == "▼"

    def test_warning(self):
        assert _symbols.WARNING == "⚠"

    def test_error(self):
        assert _symbols.ERROR == "✖"

    def test_success(self):
        assert _symbols.SUCCESS == "✓"

    def test_all_are_strings(self):
        for name in _symbols.__all__:
            assert isinstance(getattr(_symbols, name), str)

    def test_all_exported(self):
        expected = {"ENABLED", "DISABLED", "OPTIMIZED", "REDUCED", "WARNING", "ERROR", "SUCCESS"}
        assert set(_symbols.__all__) == expected


# ===========================================================================
# formatter.py — OutputFormatter
# ===========================================================================

@pytest.fixture
def fmt_no_color():
    """Formatter with color disabled (deterministic output)."""
    f = OutputFormatter("TEST", mode=OutputMode.NORMAL, minimal=False)
    f.color = False
    return f


@pytest.fixture
def fmt_color():
    """Formatter with color enabled."""
    f = OutputFormatter("TEST", mode=OutputMode.NORMAL, minimal=False)
    f.color = True
    return f


class TestOutputFormatterInit:
    """OutputFormatter stores constructor arguments correctly."""

    def test_section_stored(self):
        f = OutputFormatter("MySection")
        assert f.section == "MySection"

    def test_default_mode_normal(self):
        f = OutputFormatter("s")
        assert f.mode is OutputMode.NORMAL

    def test_explicit_mode(self):
        f = OutputFormatter("s", mode=OutputMode.RAW)
        assert f.mode is OutputMode.RAW

    def test_default_minimal_false(self):
        f = OutputFormatter("s")
        assert f.minimal is False

    def test_explicit_minimal_true(self):
        f = OutputFormatter("s", minimal=True)
        assert f.minimal is True

    def test_color_reflects_terminal(self):
        """color attribute is set from supports_color() at init."""
        with patch("tokenpak.compression.formatting.formatter.supports_color", return_value=True):
            f = OutputFormatter("s")
            assert f.color is True
        with patch("tokenpak.compression.formatting.formatter.supports_color", return_value=False):
            f = OutputFormatter("s")
            assert f.color is False


class TestOutputFormatterHeader:
    """header() produces a two-line banner."""

    def test_contains_tokenpak_version(self, fmt_no_color):
        assert "TOKENPAK v1.1.0" in fmt_no_color.header()

    def test_contains_section(self, fmt_no_color):
        assert "TEST" in fmt_no_color.header()

    def test_two_lines(self, fmt_no_color):
        lines = fmt_no_color.header().split("\n")
        assert len(lines) == 2

    def test_separator_line_length(self, fmt_no_color):
        lines = fmt_no_color.header().split("\n")
        assert len(lines[1]) == 40

    def test_separator_contains_dashes(self, fmt_no_color):
        lines = fmt_no_color.header().split("\n")
        assert set(lines[1]) == {"─"}


class TestOutputFormatterKv:
    """kv() formats key-value pairs with aligned columns."""

    def test_single_row(self, fmt_no_color):
        result = fmt_no_color.kv([("key", "value")])
        assert "key" in result
        assert "value" in result
        assert ":" in result

    def test_alignment(self, fmt_no_color):
        rows = [("short", "a"), ("longer_key", "b")]
        result = fmt_no_color.kv(rows)
        lines = result.split("\n")
        # Both lines should have the colon at the same position
        colon_positions = [line.index(":") for line in lines]
        assert colon_positions[0] == colon_positions[1]

    def test_empty_rows_returns_empty_string(self, fmt_no_color):
        assert fmt_no_color.kv([]) == ""

    def test_multiple_rows_newline_separated(self, fmt_no_color):
        rows = [("a", "1"), ("b", "2"), ("c", "3")]
        result = fmt_no_color.kv(rows)
        assert result.count("\n") == 2

    def test_generator_input(self, fmt_no_color):
        """kv() accepts any iterable, not just lists."""
        gen = ((k, v) for k, v in [("x", "1")])
        result = fmt_no_color.kv(gen)
        assert "x" in result and "1" in result


class TestOutputFormatterSignal:
    """signal() returns a symbol-prefixed message with optional color."""

    def test_no_color_plain_text(self, fmt_no_color):
        result = fmt_no_color.signal("✓", "done", tone="success")
        assert result == "✓ done"

    def test_color_enabled_wraps_ansi(self, fmt_color):
        result = fmt_color.signal("✓", "done", tone="success")
        assert Color.GREEN in result
        assert Color.RESET in result
        assert "✓ done" in result

    def test_tone_success_uses_green(self, fmt_color):
        result = fmt_color.signal("✓", "ok", tone="success")
        assert Color.GREEN in result

    def test_tone_warn_uses_yellow(self, fmt_color):
        result = fmt_color.signal("⚠", "caution", tone="warn")
        assert Color.YELLOW in result

    def test_tone_error_uses_red(self, fmt_color):
        result = fmt_color.signal("✖", "fail", tone="error")
        assert Color.RED in result

    def test_tone_muted_uses_dim(self, fmt_color):
        result = fmt_color.signal("·", "quiet", tone="muted")
        assert Color.DIM in result

    def test_tone_info_uses_cyan(self, fmt_color):
        result = fmt_color.signal("i", "info text", tone="info")
        assert Color.CYAN in result

    def test_unknown_tone_defaults_to_cyan(self, fmt_color):
        result = fmt_color.signal("?", "text", tone="totally-unknown")
        assert Color.CYAN in result

    def test_default_tone_is_info(self, fmt_color):
        result_default = fmt_color.signal("i", "text")
        result_info = fmt_color.signal("i", "text", tone="info")
        assert result_default == result_info


class TestOutputFormatterErrorBlock:
    """error_block() assembles a multi-line error message."""

    def test_contains_header(self, fmt_no_color):
        block = fmt_no_color.error_block("Bad input", "value out of range", "Check config")
        assert "TOKENPAK v1.1.0" in block

    def test_contains_title(self, fmt_no_color):
        block = fmt_no_color.error_block("Bad input", "reason here", "action here")
        assert "Bad input" in block

    def test_contains_reason(self, fmt_no_color):
        block = fmt_no_color.error_block("T", "MY REASON", "action")
        assert "MY REASON" in block

    def test_contains_action(self, fmt_no_color):
        block = fmt_no_color.error_block("T", "reason", "DO THIS")
        assert "DO THIS" in block

    def test_reason_label(self, fmt_no_color):
        block = fmt_no_color.error_block("T", "r", "a")
        assert "Reason:" in block

    def test_action_label(self, fmt_no_color):
        block = fmt_no_color.error_block("T", "r", "a")
        assert "Action:" in block

    def test_error_symbol_present(self, fmt_no_color):
        block = fmt_no_color.error_block("T", "r", "a")
        assert "✖" in block


class TestOutputFormatterMinimalLine:
    """minimal_line() joins cells with ' | '."""

    def test_basic_join(self, fmt_no_color):
        assert fmt_no_color.minimal_line(["a", "b", "c"]) == "a | b | c"

    def test_single_cell(self, fmt_no_color):
        assert fmt_no_color.minimal_line(["only"]) == "only"

    def test_non_string_cells_coerced(self, fmt_no_color):
        result = fmt_no_color.minimal_line([1, 2.5, True])
        assert result == "1 | 2.5 | True"

    def test_empty_list(self, fmt_no_color):
        assert fmt_no_color.minimal_line([]) == ""

    def test_generator_input(self, fmt_no_color):
        gen = (x for x in ["x", "y"])
        assert fmt_no_color.minimal_line(gen) == "x | y"


class TestOutputFormatterRaw:
    """raw() serialises a dict to sorted, indented JSON."""

    def test_output_is_valid_json(self, fmt_no_color):
        payload = {"b": 2, "a": 1}
        result = fmt_no_color.raw(payload)
        parsed = json.loads(result)
        assert parsed == payload

    def test_keys_are_sorted(self, fmt_no_color):
        payload = {"z": 26, "a": 1, "m": 13}
        result = fmt_no_color.raw(payload)
        # First key in output should be "a"
        assert result.index('"a"') < result.index('"m"') < result.index('"z"')

    def test_indented_with_2_spaces(self, fmt_no_color):
        payload = {"k": "v"}
        result = fmt_no_color.raw(payload)
        assert "  " in result  # at least 2-space indent present

    def test_empty_dict(self, fmt_no_color):
        result = fmt_no_color.raw({})
        assert json.loads(result) == {}

    def test_nested_dict(self, fmt_no_color):
        payload = {"outer": {"inner": 42}}
        result = fmt_no_color.raw(payload)
        assert json.loads(result) == payload


# ===========================================================================
# __init__.py — public API surface
# ===========================================================================

class TestPublicAPI:
    """formatting package exposes correct public names."""

    def test_output_formatter_exported(self):
        from tokenpak.compression.formatting import OutputFormatter as OF
        assert OF is OutputFormatter

    def test_output_mode_exported(self):
        from tokenpak.compression.formatting import OutputMode as OM
        assert OM is OutputMode

    def test_resolve_mode_exported(self):
        from tokenpak.compression.formatting import resolve_mode as rm
        assert rm is resolve_mode
