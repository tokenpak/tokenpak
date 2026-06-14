# SPDX-License-Identifier: Apache-2.0
"""CI invariants for the v1.8.0 CLI menu renderer foundation (cumulative spec H).

Covers:
- H1 pure frame-builder snapshot frames (default / search / no-results / minimal
  / no-color).
- H2 every ``\\033[?1049h`` is balanced by exactly one ``\\033[?1049l`` across all
  exit paths (normal / suspend-exit / suspend-resume / exception).
- H3 a disabled (non-TTY / CI / --no-tui) session emits ZERO ``\\033[?1049``
  sequences; ``_interactive_menu_allowed`` is False for each opt-out.
- H4 overlay-keys ⊆ real commands (E3); lifecycle dispatch maps correctly;
  ``--json`` schema stability.
- §5.3 / D7 honesty: the status strip never fabricates ``$0.00``.
"""

from __future__ import annotations

import io
import json
import sys

import pytest

from tokenpak._formatting import picker
from tokenpak.cli.commands import menu as menumod
from tokenpak.cli.commands import menu_status
from tokenpak.cli.commands.menu_lifecycle import (
    LIFECYCLE,
    Lifecycle,
    lifecycle_for,
    next_chain,
    receipt_card,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _capture(fn):
    """Run *fn* with stdout captured; return the captured string."""
    cap = io.StringIO()
    old = sys.stdout
    sys.stdout = cap
    try:
        fn()
    finally:
        sys.stdout = old
    return cap.getvalue()


def _count_1049(buf: str) -> tuple[int, int]:
    return buf.count("\033[?1049h"), buf.count("\033[?1049l")


# ---------------------------------------------------------------------------
# H1 — pure frame-builder snapshots
# ---------------------------------------------------------------------------

def test_h1_default_frame_has_selection_marker_and_no_ansi_when_color_off():
    lines = picker._compose_frame(
        title="What do you want to do?",
        subtitle="Type to search",
        header="",
        footer="",
        filter_text="",
        filterable=True,
        rows=[("Start proxy", True), ("Run demo", False)],
        scroll_above=0,
        scroll_below=0,
        no_results=False,
        color=False,
        minimal=False,
        back_label=None,
    )
    body = "\n".join(lines)
    assert any(ln.strip().startswith("> Start proxy") for ln in lines)
    assert any(ln.strip() == "Run demo" for ln in lines)  # non-selected, no marker
    assert "\033" not in body  # color=False -> zero ANSI


def test_h1_search_frame_shows_filter_and_footer():
    lines = picker._compose_frame(
        title="All commands",
        subtitle="Type to search",
        header="",
        footer="[enter] select   [esc] back   [q] quit",
        filter_text="cost",
        filterable=True,
        rows=[("View spend & savings", True)],
        scroll_above=0,
        scroll_below=0,
        no_results=False,
        color=False,
        minimal=False,
        back_label="Back",
    )
    body = "\n".join(lines)
    assert "Filter: cost_" in body
    assert "[enter] select" in body


def test_h1_no_results_frame():
    lines = picker._compose_frame(
        title="All commands",
        subtitle="",
        header="",
        footer="",
        filter_text="zzzzz",
        filterable=True,
        rows=[],
        scroll_above=0,
        scroll_below=0,
        no_results=True,
        color=False,
        minimal=False,
        back_label=None,
    )
    body = "\n".join(lines)
    assert "No matching commands found" in body
    assert "[esc] clear search" in body


def test_h1_minimal_mode_strips_chrome():
    full = picker._compose_frame(
        title="Menu", subtitle="sub", header="HEADER\nLINE2", footer="footer",
        filter_text="", filterable=False, rows=[("A", True), ("B", False)],
        scroll_above=2, scroll_below=3, no_results=False, color=False,
        minimal=False, back_label=None,
    )
    minimal = picker._compose_frame(
        title="Menu", subtitle="sub", header="HEADER\nLINE2", footer="footer",
        filter_text="", filterable=False, rows=[("A", True), ("B", False)],
        scroll_above=2, scroll_below=3, no_results=False, color=False,
        minimal=True, back_label=None,
    )
    full_body, min_body = "\n".join(full), "\n".join(minimal)
    # chrome present in full, absent in minimal
    assert "HEADER" in full_body and "HEADER" not in min_body
    assert "sub" in full_body and "sub" not in min_body
    assert "more above" in full_body and "more above" not in min_body
    # the choices themselves survive in minimal (with the marker)
    assert any(ln.strip().startswith("> A") for ln in minimal)


def test_h1_render_plain_list_strips_ansi():
    out = picker.render_plain_list("Pick one", [("a", "\033[32mGreen\033[0m"), ("b", "Plain")])
    assert "1) Green" in out
    assert "2) Plain" in out
    assert "\033" not in out


# ---------------------------------------------------------------------------
# H2 — alt-screen enter/leave balance across every exit path
# ---------------------------------------------------------------------------

def test_h2_balance_normal_exit():
    def run():
        with picker.AltScreenSession(enabled=True):
            sys.stdout.write("frame")
    h, l = _count_1049(_capture(run))
    assert (h, l) == (1, 1)


def test_h2_balance_suspend_then_exit():
    # run_and_exit shape: leave alt-screen, never resume, exit.
    def run():
        sess = picker.AltScreenSession(enabled=True)
        sess.__enter__()
        sess.suspend()
        sess.__exit__(None, None, None)
    h, l = _count_1049(_capture(run))
    assert (h, l) == (1, 1)


def test_h2_balance_suspend_resume_exit():
    # suspend_and_return shape: leave, run, re-enter, later exit.
    def run():
        with picker.AltScreenSession(enabled=True) as sess:
            sess.suspend()
            sess.resume()
    h, l = _count_1049(_capture(run))
    assert (h, l) == (2, 2)


def test_h2_balance_on_exception():
    def run():
        try:
            with picker.AltScreenSession(enabled=True):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
    h, l = _count_1049(_capture(run))
    assert (h, l) == (1, 1)


# ---------------------------------------------------------------------------
# H3 — disabled session and non-interactive gating emit no alt-screen
# ---------------------------------------------------------------------------

def test_h3_disabled_session_emits_zero_1049():
    def run():
        with picker.AltScreenSession(enabled=False) as sess:
            sess.suspend()
            sess.resume()
            sys.stdout.write("x")
    h, l = _count_1049(_capture(run))
    assert (h, l) == (0, 0)


@pytest.mark.parametrize("env", ["CI", "TOKENPAK_NONINTERACTIVE"])
def test_h3_interactive_disallowed_by_env(monkeypatch, env):
    from tokenpak import _cli_core

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(_cli_core, "_NO_TUI_FLAG", False, raising=False)
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setenv(env, "1")
    assert _cli_core._interactive_menu_allowed() is False


def test_h3_interactive_disallowed_by_term_dumb(monkeypatch):
    from tokenpak import _cli_core

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(_cli_core, "_NO_TUI_FLAG", False, raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("TOKENPAK_NONINTERACTIVE", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert _cli_core._interactive_menu_allowed() is False


def test_h3_interactive_disallowed_by_no_tui(monkeypatch):
    from tokenpak import _cli_core

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("TOKENPAK_NONINTERACTIVE", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setattr(_cli_core, "_NO_TUI_FLAG", True, raising=False)
    assert _cli_core._interactive_menu_allowed() is False


# ---------------------------------------------------------------------------
# H4 — overlay-keys subset of real commands; lifecycle map; --json schema
# ---------------------------------------------------------------------------

def test_h4_lifecycle_overlay_keys_are_real_commands():
    """E3: every lifecycle overlay key must resolve to a real CLI command."""
    from tokenpak._cli_core import _core_command_names

    real = _core_command_names()
    stale = [k for k in LIFECYCLE if k not in real]
    assert stale == [], f"lifecycle overlay references non-existent commands: {stale}"


def test_h4_lifecycle_assignments():
    assert lifecycle_for("status") is Lifecycle.RUN_AND_EXIT
    assert lifecycle_for("cost") is Lifecycle.RUN_AND_EXIT
    assert lifecycle_for("doctor") is Lifecycle.RUN_AND_EXIT
    assert lifecycle_for("start") is Lifecycle.RUN_AND_RETURN
    assert lifecycle_for("stop") is Lifecycle.RUN_AND_RETURN
    assert lifecycle_for("config") is Lifecycle.RUN_AND_RETURN
    assert lifecycle_for("demo") is Lifecycle.SUSPEND_AND_RETURN
    assert lifecycle_for("claude") is Lifecycle.TAKEOVER
    assert lifecycle_for("codex") is Lifecycle.TAKEOVER
    # C5 default for the long tail
    assert lifecycle_for("totally-unknown-cmd") is Lifecycle.RUN_AND_EXIT
    assert lifecycle_for("") is Lifecycle.RUN_AND_EXIT
    # args after the verb don't change the lifecycle
    assert lifecycle_for("cost --week") is Lifecycle.RUN_AND_EXIT


def test_h4_json_snapshot_schema_is_stable():
    menu_status.reset_cache()
    js = menu_status.json_snapshot()
    assert js["schema_version"] == menu_status.STATUS_SCHEMA_VERSION
    assert set(js) == {"schema_version", "proxy", "cost_today", "saved_today", "port"}
    assert js["proxy"] in {"running", "stopped", "starting", "unknown"}
    # fresh process, no probe forced -> honest unknown, never fabricated
    assert js["cost_today"] is None
    assert js["saved_today"] is None


def test_h4_bare_json_emits_valid_payload(monkeypatch):
    from tokenpak import _cli_core

    menu_status.reset_cache()
    out = _capture(_cli_core._emit_bare_json)
    payload = json.loads(out)  # must be valid JSON
    assert payload["schema_version"] == 1
    assert "status" in payload and "commands" in payload
    assert isinstance(payload["commands"], list) and payload["commands"]
    # catalog is sorted/deterministic
    assert payload["commands"] == sorted(payload["commands"])


# ---------------------------------------------------------------------------
# §5.3 / D7 — honesty: the status strip never fabricates a savings figure
# ---------------------------------------------------------------------------

def test_status_strip_never_fabricates_dollar_zero(monkeypatch):
    monkeypatch.setenv("TOKENPAK_NO_COLOR", "1")
    # Force a stopped/unknown snapshot (no live proxy) regardless of host state.
    monkeypatch.setattr(
        menu_status,
        "snapshot",
        lambda **_: menu_status.ProxyStatus(state="stopped", cost=None, saved=None),
    )
    strip = menumod._status_strip()
    assert "$0.00" not in strip  # the pre-existing fabrication is gone (D7)
    assert "—" in strip          # honest unknown marker
    assert "Stopped" in strip


def test_status_strip_renders_real_values_when_known(monkeypatch):
    monkeypatch.setenv("TOKENPAK_NO_COLOR", "1")
    monkeypatch.setattr(
        menu_status,
        "snapshot",
        lambda **_: menu_status.ProxyStatus(state="running", cost=1.23, saved=4.56),
    )
    strip = menumod._status_strip()
    assert "Running" in strip
    assert "$1.23" in strip
    assert "$4.56" in strip


# ---------------------------------------------------------------------------
# I — receipt card is pure box-drawing (no Rich), honest content
# ---------------------------------------------------------------------------

def test_receipt_card_uses_unicode_box_drawing():
    card = receipt_card("Proxy started", [("Status", "Running"), ("Endpoint", "127.0.0.1:8766")])
    assert "┌" in card and "┐" in card and "└" in card and "┘" in card and "├" in card
    assert "Proxy started" in card
    assert "127.0.0.1:8766" in card
    # zero-dep: no Rich panel artifacts
    assert "Panel" not in card


def test_next_chain_format():
    assert next_chain([]) == ""
    chain = next_chain(["Launch Companion", "View savings"])
    assert chain.strip().startswith("Next:")
    assert "Launch Companion" in chain and "View savings" in chain
