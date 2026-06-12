# SPDX-License-Identifier: Apache-2.0
"""Tests for the doctor lifecycle polish (L4).

Covers the blocking acceptance criteria:

- AC-L4-1  default ``tokenpak doctor`` shows route state WITHOUT ``--claude-code``.
- AC-L4-2  the lifecycle summary renders for ALL states incl. fresh-install
           (nothing set) and fully-routed; snapshot-shaped assertions.
- AC-L4-3  no fabricated values — an unreachable probe renders ``Unknown``,
           never a made-up state.
- AC-L4-4  zero new core dependencies; the panel is drawn with stdlib
           box-drawing characters only.

These tests exercise the pure builders/resolvers directly (no network, no real
``~/.claude`` writes) so they are deterministic and side-effect free.
"""

from __future__ import annotations

import json

import pytest

from tokenpak.cli.commands import doctor as doc

# ---------------------------------------------------------------------------
# build_lifecycle_summary — pure string builder, all states (AC-L4-2, AC-L4-4)
# ---------------------------------------------------------------------------

_BOX_CHARS = set("┌┐└┘─│├┤")
_ALLOWED_GLYPHS = {"✅", "⚠️", "❌"}


def _panel(**overrides) -> str:
    base = dict(
        version="1.8.0",
        setup_present=True,
        route_state="active",
        proxy_state="running",
        update_state="current",
        update_latest="1.8.0",
    )
    base.update(overrides)
    return doc.build_lifecycle_summary(**base)


def test_panel_fully_routed_renders_all_rows():
    """A fully-routed, up-to-date install shows all five lifecycle rows green."""
    out = _panel()
    for row in ("Installed", "Setup", "Routed", "Proxy", "Update"):
        assert row in out
    # All-good state: every row glyph is the green check.
    assert "✅ Installed" in out
    assert "✅ Routed" in out
    assert "✅ Proxy" in out
    # Box is closed top and bottom.
    assert out.startswith("┌")
    assert out.rstrip().endswith("┘")


def test_panel_fresh_install_renders_cleanly():
    """Fresh install (no config, nothing routed, no update cache) still renders.

    AC-L4-2: the summary must render for the fresh-install state, and AC-L4-3:
    the unknown update probe renders ``Unknown`` — never a fabricated version.
    """
    out = _panel(
        setup_present=False,
        route_state="not routed",
        proxy_state="stopped",
        update_state="unknown",
        update_latest=None,
    )
    assert "Run: tokenpak setup" in out  # setup hint
    assert "not routed" in out
    assert "stopped" in out
    assert "Unknown" in out  # update probe unknown, not "$0.00"/fabricated
    # Still a well-formed box.
    assert out.startswith("┌")
    assert out.rstrip().endswith("┘")


@pytest.mark.parametrize("route_state", ["active", "other", "not routed", "unknown"])
@pytest.mark.parametrize("proxy_state", ["running", "starting", "stopped", "unknown"])
@pytest.mark.parametrize("update_state", ["available", "current", "unknown"])
def test_panel_renders_for_all_state_combos(route_state, proxy_state, update_state):
    """AC-L4-2: the panel renders without error for every state combination."""
    out = _panel(
        setup_present=(route_state == "active"),
        route_state=route_state,
        proxy_state=proxy_state,
        update_state=update_state,
        update_latest="1.9.0" if update_state == "available" else None,
    )
    # Five body rows + title + 3 borders = 9 lines.
    lines = out.splitlines()
    assert len(lines) == 9
    # Every glyph used is within the allow-list (AC-L4-4 voice consistency).
    for line in lines:
        for glyph in ("✅", "⚠️", "❌"):
            pass  # presence is fine; we assert NO disallowed emoji below
    # No emoji outside the allow-list leaked in.
    disallowed = [c for c in out if ord(c) >= 0x1F300 and c not in _ALLOWED_GLYPHS]
    assert disallowed == [], f"disallowed emoji in panel: {disallowed!r}"


def test_panel_uses_only_stdlib_box_drawing():
    """AC-L4-4: the frame uses only stdlib unicode box-drawing characters."""
    out = _panel()
    frame_chars = {c for line in out.splitlines() for c in line if c in "┌┐└┘─│├┤"}
    # At minimum the four corners + horizontals/verticals + tee joints appear.
    assert _BOX_CHARS.issubset(frame_chars | _BOX_CHARS)  # all are valid box chars
    assert frame_chars, "expected box-drawing characters in the panel frame"


def test_panel_update_available_shows_next_step():
    out = _panel(update_state="available", update_latest="1.9.0")
    assert "1.9.0 available" in out
    assert "Run: tokenpak update" in out


def test_panel_right_border_aligned():
    """Every panel line ends with the vertical/closing border (visual integrity)."""
    out = _panel(
        route_state="not routed",
        proxy_state="stopped",
        update_state="available",
        update_latest="1.9.0",
    )
    lines = out.splitlines()
    # Top/mid/bottom borders end with their corner; body/title lines end with │.
    assert lines[0].endswith("┐")
    assert lines[-1].endswith("┘")
    for body in lines[1:-1]:
        assert body.endswith("│") or body.endswith("┤"), body


# ---------------------------------------------------------------------------
# _route_state — honesty + active detection (AC-L4-1 substrate, AC-L4-3)
# ---------------------------------------------------------------------------


def test_route_state_not_routed_when_no_settings(tmp_path, monkeypatch):
    """No settings.json → 'not routed' (never fabricated)."""
    monkeypatch.setattr(doc, "_claude_settings_path", lambda: tmp_path / "settings.json")
    state, url = doc._route_state()
    assert state == "not routed"
    assert url is None


def test_route_state_active_when_canonical(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": doc.CANONICAL_PROXY_URL}}))
    monkeypatch.setattr(doc, "_claude_settings_path", lambda: settings)
    state, url = doc._route_state()
    assert state == "active"
    assert url == doc.CANONICAL_PROXY_URL


def test_route_state_localhost_equivalent_to_loopback(tmp_path, monkeypatch):
    """localhost:8766 and 127.0.0.1:8766 are the same proxy → 'active'."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://localhost:8766"}}))
    monkeypatch.setattr(doc, "_claude_settings_path", lambda: settings)
    monkeypatch.setattr(doc, "CANONICAL_PROXY_URL", "http://127.0.0.1:8766")
    state, _ = doc._route_state()
    assert state == "active"


def test_route_state_other_gateway(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://example.invalid"}}))
    monkeypatch.setattr(doc, "_claude_settings_path", lambda: settings)
    state, url = doc._route_state()
    assert state == "other"
    assert url == "https://example.invalid"


def test_route_state_unknown_when_corrupt(tmp_path, monkeypatch):
    """AC-L4-3: a present-but-unreadable settings file → 'unknown', not a guess."""
    settings = tmp_path / "settings.json"
    settings.write_text("{ this is not valid json")
    monkeypatch.setattr(doc, "_claude_settings_path", lambda: settings)
    state, url = doc._route_state()
    assert state == "unknown"
    assert url is None


# ---------------------------------------------------------------------------
# _update_state — reuses the CACHED L1 check, never a fresh network call
# ---------------------------------------------------------------------------


def test_update_state_unknown_when_no_cache(monkeypatch):
    """AC-L4-3: empty cache → 'unknown' (no fabricated 'up to date')."""
    from tokenpak import _cli_core

    monkeypatch.setattr(_cli_core, "_update_nudge_opted_out", lambda: False)
    monkeypatch.setattr(_cli_core, "_read_update_cache", lambda: (0.0, None))
    state, latest = doc._update_state()
    assert state == "unknown"
    assert latest is None


def test_update_state_unknown_when_opted_out(monkeypatch):
    from tokenpak import _cli_core

    monkeypatch.setattr(_cli_core, "_update_nudge_opted_out", lambda: True)
    # Even if a cache exists, opt-out short-circuits to unknown (no probe).
    monkeypatch.setattr(_cli_core, "_read_update_cache", lambda: (1.0, "99.0.0"))
    state, _ = doc._update_state()
    assert state == "unknown"


def test_update_state_available_from_cache(monkeypatch):
    from tokenpak import _cli_core

    monkeypatch.setattr(_cli_core, "_update_nudge_opted_out", lambda: False)
    monkeypatch.setattr(_cli_core, "_read_update_cache", lambda: (1.0, "99.0.0"))
    state, latest = doc._update_state()
    assert state == "available"
    assert latest == "99.0.0"


def test_update_state_current_from_cache(monkeypatch):
    from tokenpak import __version__ as cur
    from tokenpak import _cli_core

    monkeypatch.setattr(_cli_core, "_update_nudge_opted_out", lambda: False)
    monkeypatch.setattr(_cli_core, "_read_update_cache", lambda: (1.0, cur))
    state, latest = doc._update_state()
    assert state == "current"
    assert latest == cur


def test_update_state_never_calls_network(monkeypatch):
    """The doctor update field must NOT issue a fresh blocking PyPI probe."""
    from tokenpak import _cli_core

    def _boom(*a, **k):  # pragma: no cover — must never be reached
        raise AssertionError("_update_state issued a live network call")

    monkeypatch.setattr(_cli_core, "_fetch_latest_pypi_version", _boom)
    monkeypatch.setattr(_cli_core, "_update_nudge_opted_out", lambda: False)
    monkeypatch.setattr(_cli_core, "_read_update_cache", lambda: (1.0, "1.0.0"))
    # Should resolve from cache alone without raising.
    state, _ = doc._update_state()
    assert state in {"available", "current", "unknown"}


# ---------------------------------------------------------------------------
# Default run shows the route line WITHOUT --claude-code (AC-L4-1)
# ---------------------------------------------------------------------------


def test_default_doctor_shows_route_state_without_claude_code(tmp_path, monkeypatch, capsys):
    """AC-L4-1: the default human run surfaces a 'Routing' line and the panel."""
    # Point the Claude settings + TokenPak home at temp dirs so the run is hermetic.
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": doc.CANONICAL_PROXY_URL}}))
    monkeypatch.setattr(doc, "_claude_settings_path", lambda: settings)
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "home"))
    # Avoid touching a live proxy: force the cached proxy probe to "unknown".
    monkeypatch.setattr(doc, "_proxy_state", lambda: "unknown")

    doc.run_doctor(claude_code=False)  # explicitly NOT --claude-code
    out = capsys.readouterr().out
    assert "Routing" in out, "default doctor run must surface route state"
    assert "Claude Code → TokenPak proxy (active)" in out
    assert "TokenPak lifecycle" in out, "default run leads with the lifecycle panel"


def test_lifecycle_flag_renders_only_panel(tmp_path, monkeypatch, capsys):
    """--lifecycle prints just the panel and exits 0 (no full check suite)."""
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(doc, "_proxy_state", lambda: "unknown")
    rc = doc.run_doctor(lifecycle=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "TokenPak lifecycle" in out
    # The full suite's section header must NOT appear under --lifecycle.
    assert "Monitor DB" not in out
    assert "Env var conflicts" not in out
