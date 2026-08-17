# SPDX-License-Identifier: Apache-2.0
"""Session-economics surface tests: status line/block/JSON adapters over the shared renderer.

The adapters must stay thin: values come from the proxy endpoint payload,
validated by ``SessionEconomics.from_dict``, projected by the shared
renderer. Goldens here pin the truth-preserving marks (plain=observed,
``~``=estimate, words=no value) and the config-disable contract: ``false``
suppresses only the default human display; ``--json`` stays available.
"""

from __future__ import annotations

import json

import pytest

from tests.session_economics_fixtures import learning_payload, no_data_payload
from tokenpak.cli.commands import status as status_mod
from tokenpak.core.contracts.session_economics import SessionEconomics
from tokenpak.core.contracts.session_economics_renderer import render_block, render_line


@pytest.fixture()
def learning_econ():
    return SessionEconomics.from_dict(learning_payload())


@pytest.fixture()
def no_data_econ():
    return SessionEconomics.from_dict(no_data_payload())


# ---------------------------------------------------------------------------
# Renderer goldens (facts vs estimates vs unknowns must be visibly distinct)
# ---------------------------------------------------------------------------


def test_line_distinguishes_facts_estimates_and_states(learning_econ):
    line = render_line(learning_econ)
    assert "in 120k" in line  # observed: plain
    assert "~$1.23 est" in line  # estimated: marked
    assert "~42k/turn" in line
    assert "runway 14 turns (context_soft)" in line
    assert "guard allow" in line
    assert "forecast learning" in line


def test_line_no_data_session_is_words_not_zeros(no_data_econ):
    line = render_line(no_data_econ)
    assert "no data" in line
    assert "stable session identity is missing" in line
    assert "0" not in line  # a missing measurement never prints as a number


def test_block_reports_every_plane_and_legend(learning_econ):
    block = render_block(learning_econ)
    assert block.splitlines()[0] == "Session economics"
    assert "base no data" in block  # unknown inside an observed session
    assert "forecast       learning" in block
    assert "legend" in block


def test_block_unavailable_forecast_is_first_class(no_data_econ):
    block = render_block(no_data_econ)
    assert "forecast       unavailable" in block
    assert "stable session identity is missing" in block


# ---------------------------------------------------------------------------
# Fetch adapter: contract validation is mandatory before projection
# ---------------------------------------------------------------------------


def test_fetch_validates_payload_via_contract(monkeypatch):
    payload = learning_payload()

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode()

    monkeypatch.setattr(status_mod.urllib.request, "urlopen", lambda *a, **k: _Resp())
    econ, reason = status_mod._fetch_session_economics("http://127.0.0.1:8766")
    assert reason == ""
    assert isinstance(econ, SessionEconomics)
    assert econ.to_dict() == SessionEconomics.from_dict(payload).to_dict()


def test_fetch_rejects_invalid_payload(monkeypatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"schema_version": "session-economics/1"}).encode()

    monkeypatch.setattr(status_mod.urllib.request, "urlopen", lambda *a, **k: _Resp())
    econ, reason = status_mod._fetch_session_economics("http://127.0.0.1:8766")
    assert econ is None
    assert "contract validation" in reason


def test_fetch_proxy_down_is_honest(monkeypatch):
    def _boom(*a, **k):
        raise OSError("refused")

    monkeypatch.setattr(status_mod.urllib.request, "urlopen", _boom)
    econ, reason = status_mod._fetch_session_economics("http://127.0.0.1:8766")
    assert econ is None
    assert reason == "proxy not reachable"


# ---------------------------------------------------------------------------
# Config gate: default display only; explicit JSON read survives disable
# ---------------------------------------------------------------------------


def test_default_line_suppressed_when_disabled(monkeypatch, capsys):
    monkeypatch.setattr(status_mod, "_session_economics_enabled", lambda: False)
    called = {"fetch": 0}

    def _fetch(_base):
        called["fetch"] += 1
        return None, "x"

    monkeypatch.setattr(status_mod, "_fetch_session_economics", _fetch)
    status_mod._print_session_economics_line("http://127.0.0.1:8766")
    assert capsys.readouterr().out == ""
    assert called["fetch"] == 0  # disabled display must not even fetch


def test_default_line_renders_when_enabled(monkeypatch, capsys, learning_econ):
    monkeypatch.setattr(status_mod, "_session_economics_enabled", lambda: True)
    monkeypatch.setattr(
        status_mod, "_fetch_session_economics", lambda _base: (learning_econ, "")
    )
    status_mod._print_session_economics_line("http://127.0.0.1:8766")
    out = capsys.readouterr().out
    assert render_line(learning_econ) in out


def test_block_suppressed_when_disabled(monkeypatch, capsys):
    monkeypatch.setattr(status_mod, "_session_economics_enabled", lambda: False)
    status_mod._print_session_economics_block("http://127.0.0.1:8766")
    assert capsys.readouterr().out == ""


def test_json_ignores_display_toggle(monkeypatch, learning_econ):
    monkeypatch.setattr(status_mod, "_session_economics_enabled", lambda: False)
    monkeypatch.setattr(
        status_mod, "_fetch_session_economics", lambda _base: (learning_econ, "")
    )
    payload = status_mod._session_economics_json("http://127.0.0.1:8766")
    assert payload == learning_econ.to_dict()
    assert payload["schema_version"] == "session-economics/1"


def test_json_unavailable_is_explicit(monkeypatch):
    monkeypatch.setattr(
        status_mod,
        "_fetch_session_economics",
        lambda _base: (None, "proxy not reachable"),
    )
    payload = status_mod._session_economics_json("http://127.0.0.1:8766")
    assert payload == {"unavailable": True, "reason": "proxy not reachable"}


def test_enabled_env_var_wins(monkeypatch):
    monkeypatch.setenv("TOKENPAK_STATUS_SESSION_ECONOMICS", "false")
    assert status_mod._session_economics_enabled() is False
    monkeypatch.setenv("TOKENPAK_STATUS_SESSION_ECONOMICS", "true")
    assert status_mod._session_economics_enabled() is True
